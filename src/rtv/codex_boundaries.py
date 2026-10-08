"""Real ATIF import and interrupted-tool rollback from an existing committed checkpoint."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from rtv.checkpoints import CheckpointRef
from rtv.codex_agent import (
    CODEX_VERSION,
    HarborCodexAdapter,
    read_turn,
    session_usage,
    validate_bundle,
)
from rtv.codex_atif import export_atif, validate_atif
from rtv.codex_lifecycle import owned_turn
from rtv.codex_probe import clean_environments, command, main_container, make_environment, verify
from rtv.events import JsonlEventSink
from rtv.harbor_probe import HARBOR_VERSION, _docker, doctor, tree_digest
from rtv.storage import LocalCheckpointStore


def load_source(
    source_run: Path, task_dir: Path, model: str
) -> tuple[dict, LocalCheckpointStore, CheckpointRef]:
    report = json.loads((source_run / "report.json").read_text())
    if report.get("status") != "passed" or report.get("mode") not in {
        "codex-session-smoke",
        "codex-checkpoint",
    }:
        raise ValueError("Start from a passed codex-smoke run")
    if report.get("task_digest") != tree_digest(task_dir.resolve()):
        raise ValueError("Source task changed; cannot compare environments")
    if report.get("model") != model or report.get("cli_version") != CODEX_VERSION:
        raise ValueError("Source model or CLI version mismatch")
    reference = CheckpointRef(
        UUID(report["checkpoint"]["checkpoint_id"]), report["checkpoint"]["manifest_sha256"]
    )
    root = source_run / "checkpoints"
    if not root.is_dir():
        raise ValueError("Source checkpoint store is missing")
    store = LocalCheckpointStore(root)
    store.load(reference)
    try:
        _docker("image", "inspect", report["image_id"])
    except RuntimeError as exc:
        raise ValueError(
            "Source image is unavailable; create a new checkpoint with codex-smoke "
            "--checkpoint-only. RTV will not silently rebuild a different image."
        ) from exc
    return report, store, reference


def gate_program(nonce: str) -> str:
    # A child in its own process session models a tool descendant outliving its shell.
    child = (
        "import time; from pathlib import Path; p=Path('/workspace/uncommitted.txt'); "
        "\nwhile True:\n p.write_text(str(time.time_ns())); time.sleep(0.05)"
    )
    return (
        "import json,os,subprocess,sys,time\nfrom pathlib import Path\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True)\n"
        f"state={{'nonce':{nonce!r},'pid':os.getpid(),'child_pid':child.pid}}\n"
        "Path('/tmp/rtv-tool-ready.json').write_text(json.dumps(state))\n"
        "time.sleep(600)\n"
        "Path('/workspace/tool-finished.txt').write_text('finished')\n"
    )


async def wait_for_tool(
    env: Any, running: asyncio.Task, nonce: str, *, timeout: float = 90
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if running.done():
            # Retrieve and surface early transport/model failures before claiming an interruption.
            running.result()
            raise RuntimeError("Agent completed before the controlled tool became active")
        result = await env.exec(
            command=(
                "test -s /tmp/rtv-tool-ready.json && test -s /workspace/uncommitted.txt "
                "&& cat /tmp/rtv-tool-ready.json"
            ),
            timeout_sec=5,
        )
        if result.return_code == 0:
            state = json.loads(result.stdout)
            if state["nonce"] != nonce:
                raise ValueError("Tool readiness belongs to a different execution")
            for key in ("pid", "child_pid"):
                if type(state[key]) is not int or state[key] <= 1:
                    raise ValueError("Invalid tool process identity")
            await command(env, f"kill -0 {state['pid']} && kill -0 {state['child_pid']}")
            before = await command(env, "cat /workspace/uncommitted.txt")
            await asyncio.sleep(0.2)
            after = await command(env, "cat /workspace/uncommitted.txt")
            if before and after and before != after:
                return state
        await asyncio.sleep(0.2)
    raise TimeoutError("Real agent did not start the controlled tool in time")


async def run_boundaries(
    task_dir: Path,
    source_run: Path,
    output: Path,
    *,
    model: str,
    auth_file: Path | None,
    allow_model_calls: bool = False,
    turn_timeout: int = 240,
) -> dict[str, Any]:
    if not allow_model_calls or auth_file is None:
        raise ValueError("Provide --allow-model-calls and --auth-file for the real boundary probe")
    if not 1 <= turn_timeout <= 600:
        raise ValueError("Turn timeout must be between 1 and 600 seconds")
    adapter = HarborCodexAdapter(model=model, auth_file=auth_file)
    preflight = doctor(task_dir, expected_task="rtv/codex-session")
    if not preflight["ready"]:
        raise ValueError("Codex boundary probe preflight failed")
    source, store, checkpoint = load_source(source_run, task_dir, model)
    from harbor.models.task.task import Task

    task = Task(task_dir.resolve())
    output = output.resolve()
    if any(output == p or p in output.parents for p in (task_dir.resolve(), source_run.resolve())):
        raise ValueError("Output must be outside the task and source run")
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    sink = JsonlEventSink(output / "events.jsonl")
    source_ref = store.load(checkpoint).source
    environments = []
    running = None
    started = time.monotonic()
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "codex-m0-boundaries",
        "status": "running",
        "model": model,
        "cli_version": CODEX_VERSION,
        "harbor_version": HARBOR_VERSION,
        "preflight": preflight,
        "task_digest": tree_digest(task_dir.resolve()),
        "source_checkpoint": checkpoint.to_dict(),
        "source_execution": source_ref.to_dict(),
        "image_id": source["image_id"],
        "image_ref": source.get("image_ref"),
        "billing": "existing-chatgpt-subscription",
        "cost_usd": None,
        "usage_complete": False,
        "checks": {},
        "turns": {},
        "agent_turns_attempted": 0,
        "cleanup_errors": [],
        "not_tested": [
            "worker crash / distributed fencing",
            "process memory restoration",
            "cross-agent / cross-model ATIF",
            "strict monetary admission",
        ],
    }

    def check(name: str, condition: bool):
        report["checks"][name] = condition
        if not condition:
            raise RuntimeError(f"Probe assertion failed: {name}")

    async def prepare(role: str):
        bundle = store.restore(checkpoint, output / f"{role}-restore")
        native = validate_bundle(bundle, model=model)
        env = make_environment(task, output, role, source["image_id"])
        environments.append(env)
        await env.start(force_build=False)
        check(
            f"{role}_same_image",
            _docker("inspect", "--format", "{{.Image}}", main_container(env)) == source["image_id"],
        )
        await command(env, "rm -rf /workspace && mkdir /workspace")
        await env.upload_dir(bundle / "workspace", "/workspace")
        roundtrip = output / f"{role}-roundtrip"
        roundtrip.mkdir()
        await env.download_dir("/workspace", roundtrip)
        check(f"{role}_workspace_identical", tree_digest(roundtrip) == source["workspace_digest"])
        return env, native

    async def execute(role, env, ref, prompt, **load):
        report["agent_turns_attempted"] += 1
        sink.emit("agent.turn.started", ref, {"role": role})
        result = await owned_turn(
            lambda: adapter.execute(env, prompt, **load), env, timeout=turn_timeout
        )
        report["turns"][role] = {
            "execution": ref.to_dict(),
            "session_id": result.session_id,
            "usage": result.usage,
            "session_usage": result.session_usage,
            "usage_scope": "incremental-turn" if result.usage is not None else "unknown",
        }
        sink.emit("agent.turn.completed", ref, {"role": role, "usage": result.usage})
        return result

    completion_prompt = (
        "Work only in /workspace. Finish positive_sum: sum positive values, return zero for "
        "empty input, support iterables. Recover the exact memory marker from the earlier "
        "conversation and write it to handoff_marker.txt. Check the result and stop. "
        "Do not read /logs or credentials, use network tools, or spawn agents."
    )
    try:
        async with asyncio.timeout(600 + 3 * turn_timeout):
            checkpoint_digest = tree_digest(store.root)
            atif_env, native = await prepare("atif")
            atif_path, audit = export_atif(native, output / "atif-export", model=model)
            report["atif_conversion"] = audit
            for kind in ("messages", "calls", "results"):
                check(f"atif_{kind}_preserved", audit["content"][kind]["lost_or_changed"] == 0)
            marker = json.loads((source_run / "handoff.json").read_text())["memory_marker"]
            atif_result = await execute(
                "atif", atif_env, source_ref.handoff(), completion_prompt, atif=atif_path
            )
            check(
                "atif_new_session",
                atif_result.session_id == validate_atif(atif_path, model=model).session_id
                and atif_result.session_id != source["turns"]["source"]["session_id"],
            )
            check(
                "atif_marker_recalled",
                await command(atif_env, "cat /workspace/handoff_marker.txt") == marker,
            )
            check("atif_verified", await verify(task, atif_env) == {"reward": 1.0})

            interrupted_env, native = await prepare("interrupted")
            nonce = uuid4().hex
            gate = output / "interrupt_gate.py"
            gate.write_text(gate_program(nonce))
            await interrupted_env.upload_file(gate, "/tmp/rtv_interrupt_gate.py")
            interrupted_ref = source_ref.recover()
            prompt = (
                "This is a controlled interruption experiment. Run exactly "
                "`python /tmp/rtv_interrupt_gate.py` using your terminal tool now. "
                "It deliberately waits; wait for it and do not kill it or run other commands. "
                "The test supervisor will stop the sandbox. Do not modify the task, read "
                "credentials or /logs, use network tools, or spawn agents."
            )
            running = asyncio.create_task(
                execute("interrupted", interrupted_env, interrupted_ref, prompt, native=native)
            )
            process_state = await wait_for_tool(interrupted_env, running, nonce)
            check("real_tool_and_child_writing", True)
            report["interrupted_processes"] = process_state
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
            else:
                raise RuntimeError("Expected cancellation, not successful completion")
            running = None
            state = json.loads(
                _docker("inspect", "--format", "{{json .State}}", main_container(interrupted_env))
            )
            check(
                "cancel_stopped_all_container_processes", not state["Running"] and state["Pid"] == 0
            )
            discarded = output / "discarded-workspace"
            discarded.mkdir()
            await interrupted_env.download_dir("/workspace", discarded)
            check("interrupted_changes_exist", (discarded / "uncommitted.txt").exists())
            check("tool_did_not_finish", not (discarded / "tool-finished.txt").exists())
            try:
                read_turn(interrupted_env.trial_paths.agent_dir, native=native)
            except (ValueError, OSError):
                check("incomplete_turn_rejected", True)
            else:
                raise RuntimeError("Interrupted turn incorrectly accepted as completed")
            observed = None
            # Diagnostic only, never accepted as a recoverable session/checkpoint.
            try:
                partial = output / "discarded-sessions"
                partial.mkdir()
                await interrupted_env.download_dir("/tmp/codex-home/sessions", partial)
                rollouts = list(partial.rglob("rollout-*.jsonl"))
                if len(rollouts) == 1:
                    observed = session_usage(rollouts[0])
            except (OSError, ValueError, RuntimeError):
                pass
            report["turns"]["interrupted"] = {
                "execution": interrupted_ref.to_dict(),
                "status": "cancelled",
                "usage": None,
                "usage_scope": "unknown",
                "observed_session_usage_lower_bound": observed,
                "checkpoint_committed": False,
            }
            sink.emit("agent.turn.cancelled", interrupted_ref, {"checkpoint_committed": False})
            check("checkpoint_unchanged_after_cancel", tree_digest(store.root) == checkpoint_digest)

            recovery_env, native = await prepare("recovery")
            check(
                "uncommitted_changes_not_restored",
                await command(
                    recovery_env,
                    "test ! -e /workspace/uncommitted.txt "
                    "&& test ! -e /workspace/tool-finished.txt && echo clean",
                )
                == "clean",
            )
            recovered = await execute(
                "recovery",
                recovery_env,
                interrupted_ref.recover(),
                completion_prompt,
                native=native,
            )
            check(
                "recovery_original_session",
                recovered.session_id == source["turns"]["source"]["session_id"],
            )
            check(
                "recovery_marker_recalled",
                await command(recovery_env, "cat /workspace/handoff_marker.txt") == marker,
            )
            check("recovery_verified", await verify(task, recovery_env) == {"reward": 1.0})
            check("source_checkpoint_unchanged", tree_digest(store.root) == checkpoint_digest)
            report["status"] = "passed"
    except asyncio.CancelledError:
        report.update(status="interrupted", error="Boundary probe cancelled")
        raise
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        if running is not None:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        await clean_environments(environments, report)
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
