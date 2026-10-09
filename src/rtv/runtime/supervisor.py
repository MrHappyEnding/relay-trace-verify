"""Detached local execution owner. One launch intent; never replay a lost turn."""

import asyncio
import os
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

from rtv.harbor_probe import FINAL_SOURCE, tree_digest
from rtv.runtime import sandbox
from rtv.runtime.config import TERMINAL, Settings, task_path
from rtv.runtime.files import admission, atomic_json, lock
from rtv.runtime.store import Store


def empty_result(row: dict, reason: str) -> dict:
    fixture = row["spec"]["adapter"] == "fixture"
    return {
        "status": "failed",
        "reason": reason,
        "usage": None,
        "usage_status": "not_applicable" if fixture else "unknown",
        "cost_usd": 0 if fixture else None,
        "billing": "no-model" if fixture else "existing-chatgpt-subscription",
        "artifacts": row["trial_id"],
    }


async def execute(settings: Settings, row: dict, output: Path) -> dict:
    from harbor.models.task.task import Task

    from rtv.codex_agent import HarborCodexAdapter
    from rtv.codex_probe import main_container, make_environment, verify
    from rtv.harbor_probe import _docker

    store = Store(settings)
    spec = row["spec"]
    result = empty_result(row, "execution_failed")
    task_dir = task_path(spec["adapter"])
    if tree_digest(task_dir) != spec["task_digest"]:
        raise ValueError("Task definition changed after submission")
    adapter = None
    if spec["adapter"] == "codex":
        if os.environ.get("RTV_ALLOW_MODEL_CALLS") != "1":
            raise ValueError("Worker requires RTV_ALLOW_MODEL_CALLS=1 for subscription calls")
        adapter = HarborCodexAdapter(
            model=spec["model"], auth_file=Path(os.environ["RTV_CODEX_AUTH_FILE"])
        )
    task = Task(task_dir)
    env = make_environment(
        task,
        output,
        "sandbox",
        spec["image_id"],
        session_id=sandbox.session(row),
        environment_type=sandbox.bounded_environment_type(),
    )
    # Startup is drained before cancellation: canceling an exec client alone may
    # leave a late Compose create. Prebuilt images avoid builds in this window.
    store.phase(row["trial_id"], "running", {"supervisor_pid": os.getpid()})
    await env.start(force_build=False)
    container = main_container(env)
    if _docker("inspect", "--format", "{{.Image}}", container) != spec["image_id"]:
        raise ValueError("Sandbox image identity changed")
    store.event(
        row["trial_id"],
        "sandbox.ready",
        {"container": container, "session": env.session_id, "supervisor_pid": os.getpid()},
    )

    def abort_reason():
        current = store.get(row["trial_id"])
        if current["cancel_requested"]:
            return "cancelled"
        if datetime.now(UTC) >= current["deadline"]:
            return "ttl_expired"
        return None

    async def body():
        reason = await asyncio.to_thread(abort_reason)
        if reason:
            result["reason"] = reason
            return
        store.event(row["trial_id"], "agent.started", {"adapter": spec["adapter"]})
        if adapter is not None:
            turn = await adapter.execute(
                env,
                "Work only in /workspace. Fix positive_sum.py so positive_sum adds only "
                "positive numbers and returns zero for empty inputs, including generators. "
                "Check the result, then stop. Do not read /logs or credentials, use network "
                "tools, or spawn other agents.",
            )
            result.update(
                usage=turn.usage,
                usage_status="known" if turn.usage is not None else "unknown",
                session_id=turn.session_id,
            )
        else:
            # A real container command with a controlled delay, explicitly model-free.
            code = (
                "import time; from pathlib import Path; "
                "Path('/workspace/agent-started').open('a').write('one\\n'); "
                f"time.sleep({spec['delay_seconds']}); "
                f"Path('/workspace/positive_sum.py').write_text({FINAL_SOURCE!r})"
            )
            reply = await env.exec(
                command="python -c " + shlex.quote(code), timeout_sec=spec["ttl_seconds"] + 30
            )
            if reply.return_code:
                raise RuntimeError("Fixture execution failed")
        store.event(
            row["trial_id"],
            "agent.completed",
            {
                "usage": result["usage"],
                "usage_status": result["usage_status"],
                "cost_usd": result["cost_usd"],
                "billing": result["billing"],
            },
        )
        store.phase(row["trial_id"], "verifying", {})
        result["rewards"] = await verify(task, env)
        # Only /workspace is exported; auth and session files are outside it.
        await asyncio.to_thread(sandbox.stop, row)
        workspace = output / "workspace"
        workspace.mkdir()
        await env.download_dir("/workspace", workspace)
        result["workspace_digest"] = tree_digest(workspace)
        result["status"] = "succeeded" if result["rewards"] == {"reward": 1.0} else "failed"
        result["reason"] = "verified" if result["status"] == "succeeded" else "verification_failed"

    job = asyncio.create_task(body())
    try:
        while not job.done():
            await asyncio.wait({job}, timeout=0.5)
            if job.done():
                break
            reason = await asyncio.to_thread(abort_reason)
            if reason:
                result["reason"] = reason
                # Quiesce remote processes before canceling local transport.
                await asyncio.to_thread(sandbox.stop, row)
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                return result
        await job
        return result
    except BaseException:
        await asyncio.to_thread(sandbox.stop, row)
        job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        raise


def run(trial_id: str):
    settings = Settings.load()
    store = Store(settings)
    row = store.get(trial_id)
    output = settings.root / trial_id
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with admission(settings.root, int(os.environ.get("RTV_MAX_EXECUTIONS", "2"))) as admitted:
        if not admitted:
            return
        _run_locked(settings, store, row, output)


def _run_locked(settings, store, row, output):
    trial_id = row["trial_id"]
    with lock(output / "runner.lock") as acquired:
        if not acquired or (output / "started.json").exists() or (output / "result.json").exists():
            return
        # Re-read cancellation under the execution lock before any external effect.
        row = store.get(trial_id)
        if row["status"] in TERMINAL:
            return
        atomic_json(
            output / "started.json",
            {
                "execution_id": row["execution_id"],
                "pid": os.getpid(),
                "at": datetime.now(UTC).isoformat(),
            },
        )
        result = empty_result(row, "cancelled" if row["cancel_requested"] else "ttl_expired")
        try:
            if not row["cancel_requested"] and datetime.now(UTC) < row["deadline"]:
                result = asyncio.run(execute(settings, row, output))
        except Exception as exc:
            # Full local trace is private. Only the exception type enters metadata/history.
            import traceback

            traceback.print_exc()
            result = empty_result(row, type(exc).__name__)
        atomic_json(output / "result.json", result)
        # If cleanup or metadata publication fails, the worker/reconciler retries it.
        sandbox.cleanup(row)
        store.finish(trial_id, result)


if __name__ == "__main__":
    run(sys.argv[1])
