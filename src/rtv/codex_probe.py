"""Opt-in, three-turn real Codex probe. This is not a benchmark or a worker service."""

import asyncio
import json
import shlex
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from rtv.codex_agent import CODEX_VERSION, HarborCodexAdapter, validate_bundle
from rtv.domain import ExecutionRef
from rtv.events import JsonlEventSink
from rtv.harbor_probe import HARBOR_VERSION, _container_ids, _docker, doctor, tree_digest
from rtv.storage import LocalCheckpointStore


def make_environment(task: Any, output: Path, role: str, image_id: str | None = None) -> Any:
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.trial.paths import EnvironmentPaths, TrialPaths

    paths = TrialPaths(output / role)
    paths.mkdir()
    config = task.config.environment
    if image_id:
        config = config.model_copy(update={"docker_image": image_id})
    remote = EnvironmentPaths.for_os(config.os)
    return DockerEnvironment(
        environment_dir=task.paths.environment_dir,
        environment_name=task.short_name,
        session_id=f"rtv-codex-{uuid4().hex}",
        trial_paths=paths,
        task_env_config=config,
        network_policy=config.resolve_baseline(),
        mounts=[
            {"type": "bind", "source": str(host), "target": str(target)}
            for host, target in [
                (paths.agent_dir, remote.agent_dir),
                (paths.verifier_dir, remote.verifier_dir),
            ]
        ],
    )


def main_container(env: Any) -> str:
    ids = _container_ids(env.session_id, main_only=True)
    if len(ids) != 1:
        raise RuntimeError("Expected exactly one main container")
    return ids[0]


async def command(env: Any, program: str) -> str:
    result = await env.exec(command=program, timeout_sec=30)
    if result.return_code:
        raise RuntimeError("Container command failed; inspect local logs")
    return (result.stdout or "").strip()


async def verify(task: Any, env: Any) -> dict[str, float]:
    from harbor.models.task.config import NetworkMode, NetworkPolicy
    from harbor.verifier.verifier import Verifier

    await env.set_network_policy(NetworkPolicy(network_mode=NetworkMode.NO_NETWORK))
    result = await asyncio.wait_for(Verifier(task, env.trial_paths, env).verify(), timeout=30)
    return result.rewards


async def clean_environments(environments: list[Any], report: dict[str, Any]) -> None:
    for env in reversed(environments):
        try:
            # Harbor delete=True runs down --rmi local, which can remove an image
            # by ID along with extra tags. In this volume-free fixture, down alone
            # removes containers/networks while retaining checkpoint image artifacts.
            await asyncio.wait_for(env.stop(delete=not bool(report.get("image_ref"))), timeout=60)
            if _container_ids(env.session_id):
                raise RuntimeError("Container cleanup left live or stopped containers")
        except Exception as exc:
            report["cleanup_errors"].append({"session": env.session_id, "error": str(exc)})
    if report.get("image_ref"):
        try:
            for reference in (report["image_id"], report["image_ref"]):
                _docker("image", "inspect", reference)
        except Exception as exc:
            report["cleanup_errors"].append({"image": report["image_ref"], "error": str(exc)})
    if report["cleanup_errors"]:
        report["status"] = "failed"


async def run_probe(
    task_dir: Path,
    output: Path,
    *,
    model: str,
    auth_file: Path | None = None,
    allow_model_calls: bool = False,
    prepare_only: bool = False,
    checkpoint_only: bool = False,
    turn_timeout: int = 240,
) -> dict[str, Any]:
    if not prepare_only and not allow_model_calls:
        raise ValueError("Real turns require explicit --allow-model-calls (subscription usage)")
    if not 1 <= turn_timeout <= 600:
        raise ValueError("Turn timeout must be between 1 and 600 seconds")
    adapter = None
    if not prepare_only:
        if auth_file is None:
            raise ValueError("Provide --auth-file pointing to a ChatGPT login cache")
        adapter = HarborCodexAdapter(model=model, auth_file=auth_file)
    preflight = doctor(task_dir, expected_task="rtv/codex-session")
    if not preflight["ready"]:
        raise ValueError(f"Codex probe preflight failed: {json.dumps(preflight)}")
    from harbor.models.task.task import Task

    task = Task(task_dir.resolve())
    output = output.resolve()
    if output == task_dir.resolve() or task_dir.resolve() in output.parents:
        raise ValueError("Output must be outside the task definition")
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    source_ref = ExecutionRef(trial_id=uuid4())
    sink = JsonlEventSink(output / "events.jsonl")
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": (
            "codex-prepare"
            if prepare_only
            else "codex-checkpoint"
            if checkpoint_only
            else "codex-session-smoke"
        ),
        "status": "running",
        "cli_version": CODEX_VERSION,
        "harbor_version": HARBOR_VERSION,
        "model": model,
        "task_digest": tree_digest(task_dir.resolve()),
        "preflight": preflight,
        "billing": "no-model" if prepare_only else "existing-chatgpt-subscription",
        "cost_usd": None,
        "agent_turns_attempted": 0,
        "agent_turns_completed": 0,
        "turns": {},
        "checks": {},
        "cleanup_errors": [],
        "not_tested": [
            "mid-tool process memory recovery",
            "worker crash / distributed ownership",
            "ATIF import",
            "second agent implementation",
            "cross-model recovery",
            "strict token or monetary admission",
            "benchmark performance",
        ],
    }
    environments: list[Any] = []
    start = time.monotonic()

    def check(name: str, condition: bool) -> None:
        report["checks"][name] = condition
        if not condition:
            raise RuntimeError(f"Probe assertion failed: {name}")

    async def new_environment(role: str, image_id: str | None = None) -> Any:
        env = make_environment(task, output, role, image_id)
        environments.append(env)  # Include partly started sandboxes in cleanup.
        await env.start(force_build=False)
        return env

    async def turn(role: str, env: Any, ref: ExecutionRef, prompt: str, native=None):
        from rtv.codex_lifecycle import owned_turn

        assert adapter is not None
        report["agent_turns_attempted"] += 1
        sink.emit("agent.turn.started", ref, {"role": role, "native_import": native is not None})
        began = time.monotonic()
        try:
            result = await owned_turn(
                lambda: adapter.execute(env, prompt, native=native), env, timeout=turn_timeout
            )
        except BaseException:
            sink.emit("agent.turn.aborted", ref, {"role": role})
            raise
        report["agent_turns_completed"] += 1
        report["turns"][role] = {
            "execution": ref.to_dict(),
            "session_id": result.session_id,
            "usage": result.usage,
            "session_usage": result.session_usage,
            "usage_scope": "incremental-turn" if result.usage is not None else "unknown",
            "seconds": round(time.monotonic() - began, 2),
        }
        sink.emit("agent.turn.completed", ref, {"role": role, "usage": result.usage})
        return result

    try:
        async with asyncio.timeout(900 + 3 * turn_timeout):
            source = await new_environment("source")
            image_id = _docker("inspect", "--format", "{{.Image}}", main_container(source))
            report["image_id"] = image_id
            actual = await command(source, "codex --version")
            check("pinned_cli", actual == f"codex-cli {CODEX_VERSION}")
            image_ref = "rtv-checkpoint-cache:" + image_id.removeprefix("sha256:")
            _docker("image", "tag", image_id, image_ref)
            report["image_ref"] = image_ref
            if prepare_only:
                report["status"] = "passed"
                return report

            marker = "rtv-memory-" + uuid4().hex
            first = await turn(
                "source",
                source,
                source_ref,
                "This is a controlled two-stage session-transfer experiment, not an open-ended "
                "coding task. Work only in /workspace. In this FIRST stage, edit positive_sum.py "
                "so positive_sum(values) sums only values greater than zero, but intentionally "
                "returns None for an empty list. Keep this staged defect for the next turn. "
                "Do not create other files or fix the empty-list case. Remember this exact marker "
                f"in conversation only; never write it to any file: {marker}. "
                "Run a short check for mixed numbers and the intentional empty-list result, "
                "then stop. Do not read /logs or credentials, use network tools or spawn agents.",
            )
            check("source_partial_rejected", await verify(task, source) == {"reward": 0.0})
            # A controlled leftover child demonstrates why CLI exit is not enough.
            writer = (
                "import time; from pathlib import Path; p=Path('/workspace/quiescence.txt'); "
                "\nwhile True:\n p.write_text(str(time.time_ns())); time.sleep(0.05)"
            )
            launch = "nohup python -c " + shlex.quote(writer) + " >/tmp/rtv-writer.log 2>&1 &"
            await command(source, launch)
            await asyncio.sleep(0.2)
            before = await command(source, "cat /workspace/quiescence.txt")
            await asyncio.sleep(0.2)
            check(
                "background_writer_active",
                before != await command(source, "cat /workspace/quiescence.txt"),
            )
            container = main_container(source)
            _docker("stop", "--time", "2", container)
            state = json.loads(_docker("inspect", "--format", "{{json .State}}", container))
            check(
                "source_stopped_before_snapshot", not state["Running"] and not state["Restarting"]
            )
            cursor = sink.emit("sandbox.quiesced", source_ref, {"container": container})
            bundle = output / "bundle"
            workspace = bundle / "workspace"
            workspace.mkdir(parents=True)
            await source.download_dir("/workspace", workspace)
            stable = output / "stable-export"
            stable.mkdir()
            await source.download_dir("/workspace", stable)
            check("stopped_export_stable", tree_digest(stable) == tree_digest(workspace))
            check(
                "marker_absent_from_workspace",
                all(
                    marker.encode() not in path.read_bytes()
                    for path in workspace.rglob("*")
                    if path.is_file()
                ),
            )
            native_dir = bundle / "native"
            native_dir.mkdir()
            shutil.copy2(first.rollout, native_dir / first.rollout.name)
            (bundle / "session.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cli_version": CODEX_VERSION,
                        "model": model,
                        "session_id": first.session_id,
                    }
                )
                + "\n"
            )
            store = LocalCheckpointStore(output / "checkpoints")
            checkpoint = store.commit(
                bundle,
                source_ref,
                event_cursor=cursor,
                summary="Positive filtering done; empty-list case pending.",
            )
            report["checkpoint"] = checkpoint.to_dict()
            report["workspace_digest"] = tree_digest(workspace)
            sink.emit("checkpoint.committed", source_ref, checkpoint.to_dict())

            handoff = {
                "schema_version": 1,
                "checkpoint_id": str(checkpoint.checkpoint_id),
                "goal": "Sum only positive values; empty inputs must produce zero.",
                "observed": "Source verifier rejected the deliberately partial solution.",
                "remaining": "Fix empty input and verify mixed, empty and iterable inputs.",
                "memory_marker": marker,
                "author": "controlled RTV fixture",
            }
            (output / "handoff.json").write_text(json.dumps(handoff, indent=2) + "\n")
            if checkpoint_only:
                report["status"] = "passed"
                return report
            for role, ref in [
                ("recovery", source_ref.recover()),
                ("handoff", source_ref.handoff()),
            ]:
                restored = store.restore(checkpoint, output / f"{role}-restore")
                native = validate_bundle(restored, model=model) if role == "recovery" else None
                target = await new_environment(role, image_id)
                check(
                    f"{role}_same_image",
                    image_id
                    == _docker("inspect", "--format", "{{.Image}}", main_container(target)),
                )
                await command(target, "rm -rf /workspace && mkdir /workspace")
                await target.upload_dir(restored / "workspace", "/workspace")
                roundtrip = output / f"{role}-roundtrip"
                roundtrip.mkdir()
                await target.download_dir("/workspace", roundtrip)
                check(
                    f"{role}_workspace_identical", tree_digest(workspace) == tree_digest(roundtrip)
                )
                prompt = (
                    "Work only in /workspace. Finish the staged positive_sum task: sum positive "
                    "values and return zero for empty inputs; support iterables. Write the exact "
                    "memory marker to handoff_marker.txt. Leave quiescence.txt untouched. "
                    "Run short checks and stop. Do not read /logs or credentials, use network "
                    "tools or spawn agents. "
                )
                if native:
                    prompt += (
                        "Recover the marker from our earlier conversation; "
                        "it is not in the workspace."
                    )
                else:
                    prompt += "Use this structured handoff: " + json.dumps(handoff)
                result = await turn(role, target, ref, prompt, native)
                check(
                    f"{role}_session_identity",
                    (result.session_id == first.session_id) == (native is not None),
                )
                check(
                    f"{role}_memory_marker",
                    await command(target, "cat /workspace/handoff_marker.txt") == marker,
                )
                check(f"{role}_verified", await verify(task, target) == {"reward": 1.0})
            after = output / "source-after"
            after.mkdir()
            await source.download_dir("/workspace", after)
            check("source_isolated_from_branches", tree_digest(workspace) == tree_digest(after))
            report["status"] = "passed"
    except asyncio.CancelledError:
        report.update(status="interrupted", error="Cancelled; incomplete stage is not checkpointed")
        raise
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        await clean_environments(environments, report)
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
