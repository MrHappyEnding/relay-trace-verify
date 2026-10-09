"""One fenced physical execution per stage; recovery always uses a fresh workspace."""

import asyncio
import os
import shlex
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from rtv.harbor_probe import FINAL_SOURCE, PARTIAL_SOURCE, tree_digest
from rtv.runtime import checkpoint, sandbox
from rtv.runtime.config import TERMINAL, Settings, execution_dir, task_path
from rtv.runtime.files import admission, atomic_json, lock
from rtv.runtime.store import Fenced, Store


def empty_result(row, reason):
    fixture = row["spec"]["adapter"] in {"fixture", "mini-fixture"}
    return {
        "status": "failed",
        "reason": reason,
        "usage": None,
        "usage_status": "not_applicable" if fixture else "unknown",
        "cost_usd": 0 if fixture else None,
        "billing": "no-model"
        if fixture
        else "metered-api"
        if row["spec"]["adapter"] == "mini"
        else "existing-chatgpt-subscription",
        "artifacts": f"{row['trial_id']}/executions/{row['execution_id']}",
    }


async def execute(settings, row, output):
    from harbor.models.task.task import Task

    from rtv.codex_agent import CODEX_VERSION, HarborCodexAdapter
    from rtv.codex_probe import main_container, make_environment, verify
    from rtv.harbor_probe import _docker

    store = Store(settings)
    spec = row["spec"]
    result = empty_result(row, "execution_failed")
    task_dir = task_path(spec.get("task_name", spec["adapter"]))
    if tree_digest(task_dir) != spec["task_digest"]:
        raise ValueError("Task definition changed after submission")
    adapter = None
    if spec["adapter"] == "codex":
        if os.environ.get("RTV_ALLOW_MODEL_CALLS") != "1":
            raise ValueError("Worker requires RTV_ALLOW_MODEL_CALLS=1")
        adapter = HarborCodexAdapter(
            model=spec["model"], auth_file=Path(os.environ["RTV_CODEX_AUTH_FILE"])
        )
    native = None
    restored = output / "restored"
    if row["resume_from"]:
        cp, native = await asyncio.to_thread(checkpoint.restore, settings, row, restored)
        result["restored_checkpoint"] = cp["checkpoint_id"]
    task = Task(task_dir)
    env = make_environment(
        task,
        output,
        "sandbox",
        spec["image_id"],
        session_id=sandbox.session(row),
        environment_type=sandbox.bounded_environment_type(),
    )
    store.phase(row, "running", {"supervisor_pid": os.getpid()})
    await env.start(force_build=False)
    container = main_container(env)
    if _docker("inspect", "--format", "{{.Image}}", container) != spec["image_id"]:
        raise ValueError("Sandbox image identity changed")
    store.event(
        row,
        "sandbox.ready",
        {"container": container, "session": env.session_id, "supervisor_pid": os.getpid()},
    )
    if row["resume_from"]:
        reply = await env.exec(command="rm -rf /workspace && mkdir /workspace", timeout_sec=15)
        if reply.return_code:
            raise RuntimeError("Cannot initialize recovery workspace")
        await env.upload_dir(restored / "workspace", "/workspace")
        roundtrip = output / "roundtrip"
        roundtrip.mkdir()
        await env.download_dir("/workspace", roundtrip)
        if tree_digest(roundtrip) != tree_digest(restored / "workspace"):
            raise ValueError("Recovery workspace digest mismatch")
        store.event(
            row,
            "workspace.restored",
            {"checkpoint_id": row["resume_from"], "workspace_digest": tree_digest(roundtrip)},
        )
    operation = store.begin_call(row)
    partial = spec.get("stages", 1) == 2 and row["stage"] == 1
    turn = None
    mini = None
    from rtv.runtime.handoff import load_context, summary_payload

    context_input = load_context(settings, row)
    prompt = (
        "Work only in /workspace. Modify positive_sum.py to sum only positive values, "
        "but deliberately return None for an empty list. Keep that staged defect, check"
        " it, then stop."
        if partial
        else "Work only in /workspace. Finish positive_sum.py: sum positive values, "
        "return zero for empty inputs including generators. Check the solution, then stop."
    )
    prompt += " Do not read /logs or credentials, use network tools or spawn other agents."
    if context_input["text"]:
        prompt += "\n\n" + context_input["text"]
    store.event(
        row,
        "context.loaded",
        {"policy": context_input["policy"], "text_bytes": len(context_input["text"].encode())},
    )
    if adapter:
        turn = await adapter.execute(env, prompt, native=native)
        result.update(
            usage=turn.usage,
            usage_status="known" if turn.usage is not None else "unknown",
            session_id=turn.session_id,
            session_usage=turn.session_usage,
        )
    elif spec["adapter"] in {"mini", "mini-fixture"}:
        from rtv.mini_agent import execute_mini

        mini = await execute_mini(
            env,
            prompt,
            output,
            settings=settings,
            row=row,
            operation=operation,
            scripted=spec["adapter"] == "mini-fixture",
        )
        result.update(
            usage={},
            usage_status="per-request-ledger",
            agent_exit_status=mini.exit_status,
            model_requests=mini.api_calls,
            synthetic_model=mini.synthetic,
        )
    else:
        delay = 0 if partial else spec["delay_seconds"]
        code = (
            "import time; from pathlib import Path; "
            "Path('/workspace/agent-started').open('a').write('one\\n'); "
            "Path('/workspace/uncommitted.txt').write_text('in-flight'); "
            f"time.sleep({delay}); Path('/workspace/positive_sum.py').write_text("
            f"{PARTIAL_SOURCE if partial else FINAL_SOURCE!r}); "
            "Path('/workspace/uncommitted.txt').unlink()"
        )
        reply = await env.exec(
            command="python -c " + shlex.quote(code), timeout_sec=spec["ttl_seconds"] + 30
        )
        if reply.return_code:
            raise RuntimeError("Fixture execution failed")
    store.settle_call(row, operation, result["usage"])
    store.phase(row, "verifying", {})
    if not spec.get("isolated_verifier"):
        result["rewards"] = await verify(task, env)
    await asyncio.to_thread(sandbox.stop, row)
    ids = sandbox.resources(row)["containers"]
    store.event(row, "sandbox.quiesced", {"containers": ids})
    bundle = output / "bundle"
    workspace = bundle / "workspace"
    workspace.mkdir(parents=True)
    await env.download_dir("/workspace", workspace)
    if turn:
        (bundle / "native").mkdir()
        shutil.copy2(turn.rollout, bundle / "native" / turn.rollout.name)
        atomic_json(
            bundle / "session.json",
            {
                "schema_version": 1,
                "cli_version": CODEX_VERSION,
                "model": spec["model"],
                "session_id": turn.session_id,
            },
        )
    if spec.get("isolated_verifier"):
        from rtv.runtime.verification import isolated_verify

        result["rewards"], result["verification"] = await isolated_verify(
            task, row, workspace, output
        )
    expected = {"reward": 0.0} if partial else {"reward": 1.0}
    result["status"] = (
        ("sealed" if spec.get("pause_after_checkpoint") else "checkpointed")
        if partial
        else "succeeded"
    )
    result["reason"] = "stage_complete" if partial else "verified"
    if result["rewards"] != expected:
        result.update(status="failed", reason="verification_failed")
    if mini and mini.exit_status != "Submitted":
        result.update(status="failed", reason="agent_limit_or_error")
    if mini:
        shutil.copy2(mini.rollout, bundle / "mini-trajectory.json")
        atomic_json(
            bundle / "handoff-summary.json",
            {
                "source": "mini-swe-agent",
                "exit_status": mini.exit_status,
                "verification": "self-report only",
            },
        )
    else:
        atomic_json(bundle / "handoff-summary.json", summary_payload(turn))
    result["workspace_digest"] = tree_digest(workspace)
    context = checkpoint.boundary(settings, row, bundle, result, ids)
    cp = await asyncio.to_thread(checkpoint.publish, settings, row, bundle, context)
    return {**result, "checkpoint_id": cp["checkpoint_id"]}


async def supervise(settings, row, output):
    store = Store(settings)
    job = asyncio.create_task(execute(settings, row, output))
    last_heartbeat = 0
    try:
        while not job.done():
            await asyncio.wait({job}, timeout=0.5)
            if job.done():
                break
            if time.monotonic() - last_heartbeat >= 2:
                current = await asyncio.to_thread(store.heartbeat, row)
                last_heartbeat = time.monotonic()
                if current["cancel_requested"]:
                    raise Fenced("cancelled")
                if datetime.now(UTC) >= current["deadline"]:
                    raise TimeoutError("ttl_expired")
        return await job
    except BaseException:
        # Stop all remote processes before canceling the transport. Even a stale
        # supervisor may only address its own execution-specific sandbox.
        try:
            await asyncio.to_thread(sandbox.stop, row)
        finally:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        raise


def run(trial_id, execution_id):
    settings = Settings.load()
    store = Store(settings)
    row = store.get(trial_id)
    if row["execution_id"] != execution_id or row["status"] in TERMINAL:
        return
    output = execution_dir(settings, row)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with admission(settings.root, int(os.environ.get("RTV_MAX_EXECUTIONS", "2"))) as admitted:
        if not admitted:
            return
        with lock(output / "runner.lock") as acquired:
            if (
                not acquired
                or (output / "started.json").exists()
                or (output / "result.json").exists()
            ):
                return
            if not store.claim(row):
                return
            atomic_json(
                output / "started.json",
                {
                    "execution_id": execution_id,
                    "epoch": row["epoch"],
                    "pid": os.getpid(),
                    "at": datetime.now(UTC).isoformat(),
                },
            )
            result = empty_result(row, "execution_failed")
            try:
                result = asyncio.run(supervise(settings, row, output))
            except Exception as exc:
                import traceback

                traceback.print_exc()
                reason = "ttl_expired" if isinstance(exc, TimeoutError) else type(exc).__name__
                result = empty_result(row, reason)
            atomic_json(output / "result.json", result)
            sandbox.cleanup(row)
            try:
                store.finish(row, result)
            except Fenced:
                # This result remains a diagnostic artifact, never current state.
                pass


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
