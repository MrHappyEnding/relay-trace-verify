"""Final verification consumes only declared workspace bytes in a fresh sandbox."""

import asyncio
import json
import shlex

from rtv.codex_probe import main_container, make_environment, verify
from rtv.harbor_probe import _docker, tree_digest
from rtv.runtime import sandbox
from rtv.runtime.tasks import async_workspace_digest, contract
from rtv.runtime.tasks import workspace as workspace_scope


async def isolated_verify(task, row, workspace, output):
    from harbor.models.task.config import NetworkMode, NetworkPolicy

    declared = contract(row["spec"])
    scope = workspace_scope(row["spec"])
    image = declared["verifier_image_id"] if declared else row["spec"]["image_id"]
    env = make_environment(
        task,
        output,
        "verifier",
        image,
        session_id=sandbox.session(row) + "-verify",
        environment_type=sandbox.bounded_environment_type(
            startup_timeout_seconds=180 if declared else 60
        ),
        network_policy=NetworkPolicy(network_mode=NetworkMode.NO_NETWORK),
        platform=declared["platform"] if declared else None,
        workspace=scope,
    )
    try:
        await env.start(force_build=False)
        reply = await env.exec(command=f"rm -rf {scope} && mkdir {scope}", timeout_sec=15)
        if reply.return_code:
            raise RuntimeError("Cannot prepare isolated verifier workspace")
        await env.upload_dir(workspace, scope)
        if declared:
            # Uploads may retain the host UID. Trust only this declared task
            # repository inside the disposable verifier, never the host Git config.
            reply = await env.exec(
                command=f"git config --global --add safe.directory {shlex.quote(scope)}",
                timeout_sec=15,
            )
            if reply.return_code:
                raise RuntimeError("Cannot configure isolated verifier repository ownership")
            reply = await env.exec(
                command="find /testbed -name .DS_Store -type f -delete", timeout_sec=30
            )
            if reply.return_code:
                raise RuntimeError("Cannot remove excluded verifier input metadata")
        restored = output / "verifier-input"
        restored.mkdir()
        await env.download_dir(scope, restored)
        digest = await async_workspace_digest(workspace, row["spec"])
        if await async_workspace_digest(restored, row["spec"]) != digest:
            raise ValueError("Verifier input digest mismatch")
        container = await asyncio.to_thread(main_container, env)
        if (
            await asyncio.to_thread(_docker, "inspect", "--format", "{{.Image}}", container)
            != image
        ):
            raise ValueError("Verifier image identity changed")
        rewards = await verify(
            task, env, timeout=declared["verifier_timeout_seconds"] if declared else 30
        )
        if declared:
            # Harbor reward=0 alone can also mean that its parser crashed.
            parsed = json.loads((env.trial_paths.verifier_dir / "report.json").read_text())[
                declared["instance_id"]
            ]
            if (
                parsed.get("patch_successfully_applied") is not True
                or type(parsed.get("resolved")) is not bool
                or rewards != {"reward": float(parsed["resolved"])}
            ):
                raise ValueError("SWE-bench parser and reward disagree or tests were not parsed")
        return rewards, {
            "status": "completed",
            "isolated": True,
            "workspace_digest": digest,
            "container": container,
            "verifier_digest": await asyncio.to_thread(tree_digest, task.paths.tests_dir),
            "workspace_scope": scope,
            "image_id": image,
        }
    finally:
        ids = await asyncio.to_thread(sandbox.project_resources, sandbox.session(row) + "-verify")
        if ids["containers"]:
            await asyncio.to_thread(_docker, "rm", "-f", *ids["containers"])
        for network in ids["networks"]:
            await asyncio.to_thread(_docker, "network", "rm", network)
