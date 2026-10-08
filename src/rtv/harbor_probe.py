"""M0 integration probe. Harbor is optional; no model clients are constructed here."""

import asyncio
import hashlib
import json
import platform
import shlex
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

from rtv.domain import ExecutionRef
from rtv.events import JsonlEventSink
from rtv.storage import LocalCheckpointStore

HARBOR_VERSION = "0.24.0"
PARTIAL_SOURCE = (
    "def positive_sum(values):\n"
    "    return sum(value for value in values if value > 0) if values else None\n"
)
FINAL_SOURCE = "def positive_sum(values):\n    return sum(value for value in values if value > 0)\n"


def _docker(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"docker exited {result.returncode}")
    return result.stdout.strip()


def tree_digest(root: Path) -> str:
    """Digest names, types, permissions and bytes without following symbolic links."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Digest root must be a real directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are outside this probe's contract: {path}")
        if not path.is_file() and not path.is_dir():
            raise ValueError(f"Unsupported entry: {path}")
        entry = [path.relative_to(root).as_posix(), path.stat().st_mode & 0o777]
        entry += ["directory"] if path.is_dir() else ["file", path.stat().st_size]
        digest.update(json.dumps(entry, separators=(",", ":")).encode() + b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def doctor(task_dir: Path) -> dict[str, Any]:
    """Read-only checks. Never starts containers, pulls images or calls a model."""
    checks: dict[str, dict[str, Any]] = {}
    checks["python"] = {"ok": sys.version_info >= (3, 12), "version": platform.python_version()}
    try:
        installed = version("harbor")
    except PackageNotFoundError:
        installed = None
    checks["harbor"] = {
        "ok": installed == HARBOR_VERSION,
        "version": installed,
        "required": HARBOR_VERSION,
    }
    if checks["python"]["ok"] and checks["harbor"]["ok"]:
        try:
            from harbor.models.task.task import Task

            task = Task(task_dir)
            if task.name != "rtv/positive-sum":
                raise ValueError("This smoke probe only supports the rtv/positive-sum fixture")
            checks["task"] = {
                "ok": True,
                "name": task.name,
                "digest": tree_digest(task_dir.resolve()),
            }
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            checks["task"] = {"ok": False, "detail": str(exc)}
    else:
        checks["task"] = {"ok": False, "detail": "Install the pinned Harbor group on Python 3.12+"}
    for name, args in [
        ("docker", ("version", "--format", "{{json .}}")),
        ("compose", ("compose", "version", "--short")),
    ]:
        try:
            output = _docker(*args, timeout=10)
            if name == "docker":
                data = json.loads(output)
                server = data.get("Server") or {}
                checks[name] = {
                    "ok": server.get("Os") == "linux",
                    "client": data.get("Client", {}).get("Version"),
                    "server": server.get("Version"),
                    "arch": server.get("Arch"),
                }
            else:
                checks[name] = {"ok": bool(output), "version": output}
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            checks[name] = {"ok": False, "detail": str(exc)}
    return {
        "schema_version": 1,
        "mode": "read-only-preflight",
        "ready": all(check["ok"] for check in checks.values()),
        "checks": checks,
    }


def _container_ids(session: str, *, main_only: bool = False) -> list[str]:
    # session is a fresh RTV UUID name, never an existing user Compose project.
    filters = ["--filter", f"label=com.docker.compose.project={session}"]
    if main_only:
        filters += ["--filter", "label=com.docker.compose.service=main"]
    return _docker("ps", "-aq", *filters).split()


async def run_smoke(task_dir: Path, output: Path) -> dict[str, Any]:
    """Stop a source container, export files, then restore into a fresh Harbor sandbox.

    This verifies only a scripted file-only stage. No native session, model,
    distributed ownership or arbitrary process-memory recovery is claimed.
    """
    preflight = doctor(task_dir)
    if not preflight["ready"]:
        raise ValueError("Harbor preflight failed; run `rtv harbor-doctor` for details")
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.models.task.config import NetworkMode, NetworkPolicy
    from harbor.models.task.task import Task
    from harbor.models.trial.paths import EnvironmentPaths, TrialPaths
    from harbor.verifier.verifier import Verifier

    task_dir = task_dir.resolve()
    output = output.resolve()
    if output == task_dir or task_dir in output.parents:
        raise ValueError("Output must be outside the task definition")
    output.mkdir(parents=True, exist_ok=False)
    task = Task(task_dir)
    source_ref = ExecutionRef(trial_id=uuid4())
    target_ref = source_ref.handoff()
    sink = JsonlEventSink(output / "events.jsonl")
    store = LocalCheckpointStore(output / "checkpoints")
    environments: list[Any] = []
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "harbor-scripted-smoke",
        "status": "running",
        "model_calls": 0,
        "preflight": preflight,
        "task_digest": tree_digest(task_dir),
        "source": source_ref.to_dict(),
        "target": target_ref.to_dict(),
        "capabilities_verified": [],
        "container_images": {},
        "cleanup_errors": [],
        "not_tested": [
            "native_session",
            "atif_import",
            "real_agent_quiescence",
            "worker_recovery",
            "budget_admission",
        ],
    }

    def make_environment(role: str, *, image_id: str | None = None) -> Any:
        paths = TrialPaths(output / role)
        paths.mkdir()
        environment_config = task.config.environment
        if image_id is not None:
            # Rebuilding may change the image ID even when build layers are cached.
            # Restore against the exact source image, not another build of its inputs.
            environment_config = environment_config.model_copy(update={"docker_image": image_id})
        env_paths = EnvironmentPaths.for_os(environment_config.os)
        # Direct environment construction bypasses Trial's log-mount setup.
        mounts = [
            {"type": "bind", "source": str(host), "target": str(container)}
            for host, container in [
                (paths.agent_dir, env_paths.agent_dir),
                (paths.verifier_dir, env_paths.verifier_dir),
            ]
        ]
        env = DockerEnvironment(
            environment_dir=task.paths.environment_dir,
            environment_name=task.short_name,
            session_id=f"rtv-m0-{uuid4().hex}",
            trial_paths=paths,
            task_env_config=environment_config,
            mounts=mounts,
            network_policy=NetworkPolicy(network_mode=NetworkMode.NO_NETWORK),
        )
        environments.append(env)
        return env

    async def write_source(env: Any, content: str) -> None:
        program = (
            "from pathlib import Path; Path('/workspace/positive_sum.py').write_text("
            + repr(content)
            + ")"
        )
        result = await env.exec(command="python -c " + shlex.quote(program), timeout_sec=30)
        if result.return_code != 0:
            raise RuntimeError(f"Scripted stage failed: {result.stderr}")

    def record_images(env: Any) -> None:
        # Harbor's no-network policy can add an egress-control sidecar.
        report["container_images"][env.session_id] = {
            container: _docker("inspect", "--format", "{{.Image}}", container)
            for container in _container_ids(env.session_id)
        }

    try:
        async with asyncio.timeout(600):
            source = make_environment("source")
            await source.start(force_build=False)
            record_images(source)
            sink.emit("sandbox.started", source_ref, {"session": source.session_id})
            await write_source(source, PARTIAL_SOURCE)
            ids = _container_ids(source.session_id, main_only=True)
            if len(ids) != 1:
                raise RuntimeError("Expected exactly one source fixture container")
            report["source_image_id"] = _docker("inspect", "--format", "{{.Image}}", ids[0])
            _docker("stop", "--time", "10", ids[0])
            state = json.loads(_docker("inspect", "--format", "{{json .State}}", ids[0]))
            if state.get("Running") or state.get("Restarting"):
                raise RuntimeError("Source is not stopped; refusing to checkpoint")
            cursor = sink.emit("sandbox.stopped", source_ref, {"container": ids[0]})
            exported = output / "exported"
            exported.mkdir()
            await source.download_dir("/workspace", exported)
            if (exported / "positive_sum.py").read_text() != PARTIAL_SOURCE:
                raise RuntimeError("Export did not preserve the source stage")
            checkpoint = store.commit(
                exported,
                source_ref,
                event_cursor=cursor,
                summary="Positive filtering implemented; empty input still fails.",
            )
            sink.emit("checkpoint.committed", source_ref, checkpoint.to_dict())
            restored = store.restore(checkpoint, output / "restored")
            target = make_environment("target", image_id=report["source_image_id"])
            await target.start(force_build=False)
            record_images(target)
            target_ids = _container_ids(target.session_id, main_only=True)
            if len(target_ids) != 1:
                raise RuntimeError("Expected exactly one target fixture container")
            target_image = _docker("inspect", "--format", "{{.Image}}", target_ids[0])
            report["target_image_id"] = target_image
            if target_image != report["source_image_id"]:
                raise RuntimeError("Source and target images differ")
            result = await target.exec(
                command="rm -rf /workspace && mkdir /workspace", timeout_sec=30
            )
            if result.return_code != 0:
                raise RuntimeError("Could not prepare target workspace")
            await target.upload_dir(restored, "/workspace")
            roundtrip = output / "roundtrip"
            roundtrip.mkdir()
            await target.download_dir("/workspace", roundtrip)
            if tree_digest(exported) != tree_digest(roundtrip):
                raise RuntimeError("Restored workspace digest differs from stopped source")
            sink.emit(
                "workspace.restored", target_ref, {"checkpoint": str(checkpoint.checkpoint_id)}
            )
            # Validate the inherited partial solution before completing it.
            before = await asyncio.wait_for(
                Verifier(task, target.trial_paths, target).verify(), timeout=30
            )
            if before.rewards != {"reward": 0.0}:
                raise RuntimeError("Verifier must reject the partial solution")
            await write_source(target, FINAL_SOURCE)
            after = await asyncio.wait_for(
                Verifier(task, target.trial_paths, target).verify(), timeout=30
            )
            # The stopped source must remain independent from target writes.
            await source.download_file("/workspace/positive_sum.py", output / "source-after.py")
            if (output / "source-after.py").read_text() != PARTIAL_SOURCE:
                raise RuntimeError("Target changes polluted source files")
            if after.rewards != {"reward": 1.0}:
                raise RuntimeError("Final fixture verification failed")
            report.update(
                status="passed",
                checkpoint=checkpoint.to_dict(),
                workspace_digest=tree_digest(exported),
                verifier_before=before.rewards,
                verifier_after=after.rewards,
                capabilities_verified=[
                    "harbor_task",
                    "stopped_container_export",
                    "file_checkpoint_restore",
                    "workspace_isolation",
                    "harbor_verifier",
                ],
            )
            sink.emit("verification.completed", target_ref, {"passed": True})
    except asyncio.CancelledError:
        report.update(status="interrupted", error="Cancelled before smoke completion")
        raise
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        for env in reversed(environments):
            try:
                await asyncio.wait_for(env.stop(delete=True), timeout=60)
                remaining = _container_ids(env.session_id)
                if remaining:
                    raise RuntimeError(f"Containers remain: {remaining}")
            except Exception as exc:
                report["cleanup_errors"].append({"session": env.session_id, "error": str(exc)})
        if report["cleanup_errors"]:
            report["status"] = "failed"
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
