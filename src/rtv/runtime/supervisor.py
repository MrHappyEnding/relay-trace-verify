"""One fenced physical execution per stage; recovery always uses a fresh workspace."""

import asyncio
import os
import shlex
import shutil
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from rtv.harbor_probe import FINAL_SOURCE, PARTIAL_SOURCE, tree_digest
from rtv.runtime import checkpoint, sandbox, tasks
from rtv.runtime.config import TERMINAL, Settings, execution_dir
from rtv.runtime.files import admission, atomic_json, lock
from rtv.runtime.store import Fenced, Store


class AgentStageTimeout(RuntimeError):
    """Agent time exhausted, distinct from the absolute Trial deadline."""


async def bounded_turn(coroutine, seconds):
    timer = asyncio.timeout(seconds)
    try:
        async with timer:
            return await coroutine
    except TimeoutError as exc:
        if timer.expired():
            raise AgentStageTimeout("agent_stage_timeout") from exc
        raise


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
    task_dir = tasks.directory(spec)
    contract = tasks.contract(spec)
    scope = tasks.workspace(spec)
    if tree_digest(task_dir) != spec["task_digest"]:
        raise ValueError("Task definition changed after submission")
    adapter = None
    if spec["adapter"] == "codex":
        if os.environ.get("RTV_ALLOW_MODEL_CALLS") != "1":
            raise ValueError("Worker requires RTV_ALLOW_MODEL_CALLS=1")
        adapter = HarborCodexAdapter(
            model=spec["model"], auth_file=Path(os.environ["RTV_CODEX_AUTH_FILE"]), workspace=scope
        )
    native = None
    restored = output / "restored"
    if row["resume_from"]:
        cp, native = await asyncio.to_thread(checkpoint.restore, settings, row, restored)
        result["restored_checkpoint"] = cp["checkpoint_id"]
    task = Task(task_dir)
    network = None
    if contract:
        from harbor.models.task.config import NetworkMode, NetworkPolicy

        network = NetworkPolicy(
            network_mode=NetworkMode.ALLOWLIST if adapter else NetworkMode.NO_NETWORK,
            allowed_hosts=["chatgpt.com", "auth.openai.com", "api.openai.com"] if adapter else [],
        )
    env = make_environment(
        task,
        output,
        "sandbox",
        spec["image_id"],
        session_id=sandbox.session(row),
        environment_type=sandbox.bounded_environment_type(
            startup_timeout_seconds=180 if contract else 60,
            # The outer stage timer owns cancellation/classification. The extra
            # 30 seconds prevents a competing Compose timer from firing first.
            exec_timeout_seconds=spec.get("agent_seconds", spec["ttl_seconds"]) + 30
            if adapter
            else 60,
        ),
        platform=contract["platform"] if contract else None,
        workspace=scope,
        network_policy=network,
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
        reply = await env.exec(command=f"rm -rf {scope} && mkdir {scope}", timeout_sec=15)
        if reply.return_code:
            raise RuntimeError("Cannot initialize recovery workspace")
        await env.upload_dir(restored / "workspace", scope)
        if contract:
            reply = await env.exec(
                command="find /testbed -name .DS_Store -type f -delete", timeout_sec=30
            )
            if reply.return_code:
                raise RuntimeError("Cannot remove excluded host metadata")
        roundtrip = output / "roundtrip"
        roundtrip.mkdir()
        await env.download_dir(scope, roundtrip)
        if tasks.workspace_digest(roundtrip, spec) != tasks.workspace_digest(
            restored / "workspace", spec
        ):
            raise ValueError("Recovery workspace digest mismatch")
        store.event(
            row,
            "workspace.restored",
            {
                "checkpoint_id": row["resume_from"],
                "workspace_digest": tasks.workspace_digest(roundtrip, spec),
            },
        )
    elif contract:
        trees = await env.exec(
            command=f"git rev-parse 'HEAD^{{tree}}' '{contract['base_commit']}^{{tree}}'",
            cwd=scope,
            timeout_sec=30,
        )
        clean = await env.exec(command="git status --porcelain", cwd=scope, timeout_sec=30)
        values = (trees.stdout or "").splitlines()
        if (
            trees.return_code
            or clean.return_code
            or len(values) != 2
            or values[0] != values[1]
            or (clean.stdout or "").strip()
        ):
            raise ValueError("Fresh repository does not match the frozen clean base tree")
    operation = store.begin_call(row)
    partial = spec.get("stages", 1) == 2 and row["stage"] == 1
    turn = None
    mini = None
    from rtv.runtime.handoff import load_context, summary_payload

    context_input = load_context(settings, row)
    prompt = tasks.prompt(spec, partial)
    if context_input["text"]:
        prompt += "\n\n" + context_input["text"]
    store.event(
        row,
        "context.loaded",
        {"policy": context_input["policy"], "text_bytes": len(context_input["text"].encode())},
    )
    if adapter:
        turn = await bounded_turn(
            adapter.execute(env, prompt, native=native),
            spec.get("agent_seconds", spec["ttl_seconds"]),
        )
        result.update(
            usage=turn.usage,
            usage_status="known" if turn.usage is not None else "unknown",
            session_id=turn.session_id,
            session_usage=turn.session_usage,
        )
    elif spec["adapter"] in {"mini", "mini-fixture"}:
        from rtv.mini_agent import execute_mini

        mini = await bounded_turn(
            execute_mini(
                env,
                prompt,
                output,
                settings=settings,
                row=row,
                operation=operation,
                scripted=spec["adapter"] == "mini-fixture",
            ),
            spec.get("agent_seconds", spec["ttl_seconds"]),
        )
        result.update(
            usage={},
            usage_status="per-request-ledger",
            agent_exit_status=mini.exit_status,
            model_requests=mini.api_calls,
            synthetic_model=mini.synthetic,
        )
    elif contract:
        for command in tasks.scripted_commands(spec, partial):
            reply = await env.exec(
                command=command, cwd=scope, timeout_sec=spec.get("agent_seconds", 300)
            )
            if reply.return_code:
                raise RuntimeError("Scripted repository control failed")
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
    skip_verifier = bool(contract and partial)
    if contract:
        result["verification"] = {
            "status": "not_run",
            "reason": "hidden verification is final-only",
        }
    if not contract and not spec.get("isolated_verifier"):
        result["rewards"] = await verify(task, env)
    await asyncio.to_thread(sandbox.stop, row)
    ids = sandbox.resources(row)["containers"]
    store.event(row, "sandbox.quiesced", {"containers": ids})
    bundle = output / "bundle"
    workspace = bundle / "workspace"
    workspace.mkdir(parents=True)
    await env.download_dir(scope, workspace)
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
                "workspace_scope": scope,
            },
        )
    if not skip_verifier and (contract or spec.get("isolated_verifier")):
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
    if not skip_verifier and result["rewards"] != expected:
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
        atomic_json(
            bundle / "handoff-summary.json",
            {
                "source": "scripted repository control",
                "verification": "not a model outcome",
            }
            if contract and turn is None
            else summary_payload(turn),
        )
    result["workspace_digest"] = tasks.workspace_digest(workspace, spec)
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
    if settings.schema == "rtv4" and os.environ.get("RTV_CLUSTER_JOB") != "1":
        raise ValueError("M4 supervisors must run in an admitted cluster Job")
    slots = (
        nullcontext(True)
        if settings.schema == "rtv4"
        else admission(settings.root, int(os.environ.get("RTV_MAX_EXECUTIONS", "2")))
    )
    with slots as admitted:
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
                reason = (
                    "agent_stage_timeout"
                    if isinstance(exc, AgentStageTimeout)
                    else "ttl_expired"
                    if isinstance(exc, TimeoutError)
                    else type(exc).__name__
                )
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
