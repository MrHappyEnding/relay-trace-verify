"""Real Docker timing checks without credentials, agent processes or model calls."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from harbor.models.task.config import NetworkMode, NetworkPolicy
from harbor.models.task.task import Task

from rtv.codex_probe import make_environment
from rtv.harbor_probe import tree_digest
from rtv.runtime.sandbox import bounded_environment_type, project_resources
from rtv.runtime.supervisor import AgentStageTimeout, bounded_turn
from rtv.runtime.tasks import validate_contract


async def probe(contract, output):
    declared = validate_contract(json.loads(contract.read_text()))
    task_dir = Path(declared["task_directory"])
    if tree_digest(task_dir) != declared["task_digest"]:
        raise ValueError("Task changed")
    output.mkdir(parents=True, exist_ok=False)
    session = "rtv-limits-" + uuid4().hex
    env = make_environment(
        Task(task_dir),
        output,
        "sandbox",
        declared["agent_image_id"],
        session_id=session,
        environment_type=bounded_environment_type(
            startup_timeout_seconds=180, exec_timeout_seconds=110
        ),
        platform=declared["platform"],
        workspace=declared["workspace"],
        network_policy=NetworkPolicy(network_mode=NetworkMode.NO_NETWORK),
    )
    checks, errors = {}, []
    started = time.monotonic()
    command_seconds = None
    try:
        await env.start(force_build=False)
        turn_started = time.monotonic()
        reply = await bounded_turn(env.exec(command="sleep 65; printf completed"), 80)
        command_seconds = round(time.monotonic() - turn_started, 2)
        checks["exec_survives_old_60_second_default"] = (
            reply.return_code == 0 and reply.stdout == "completed" and command_seconds >= 65
        )
        try:
            await env.exec(command="sleep 4", timeout_sec=1)
            checks["explicit_tool_timeout"] = False
        except RuntimeError as exc:
            checks["explicit_tool_timeout"] = "Command timed out after 1 seconds" in str(exc)
        try:
            await bounded_turn(env.exec(command="sleep 4"), 0.2)
            checks["stage_timeout_classification"] = False
        except AgentStageTimeout:
            checks["stage_timeout_classification"] = True
    except Exception as exc:
        errors.append(type(exc).__name__)
    finally:
        try:
            await env.stop(delete=True)
            checks["owned_resources_removed"] = not any(project_resources(session).values())
        except Exception as exc:
            errors.append("cleanup:" + type(exc).__name__)
    report = {
        "status": "passed"
        if len(checks) == 4 and all(checks.values()) and not errors
        else "failed",
        "mode": "real-docker-no-model",
        "model_calls": 0,
        "api_spend_usd": 0,
        "contract_sha256": declared["contract_sha256"],
        "checks": checks,
        "errors": errors,
        "long_command_seconds": command_seconds,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "limit": "Host Docker/Harbor timing only; no Kubernetes runner or real agent tested.",
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(probe(args.contract, args.output)))
