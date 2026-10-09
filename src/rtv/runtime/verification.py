"""Final verification consumes only declared workspace bytes in a fresh sandbox."""

from rtv.codex_probe import main_container, make_environment, verify
from rtv.harbor_probe import _docker, tree_digest
from rtv.runtime import sandbox


async def isolated_verify(task, row, workspace, output):
    from harbor.models.task.config import NetworkMode, NetworkPolicy

    env = make_environment(
        task,
        output,
        "verifier",
        row["spec"]["image_id"],
        session_id=sandbox.session(row) + "-verify",
        environment_type=sandbox.bounded_environment_type(),
        network_policy=NetworkPolicy(network_mode=NetworkMode.NO_NETWORK),
    )
    try:
        await env.start(force_build=False)
        await env.exec(command="rm -rf /workspace && mkdir /workspace", timeout_sec=15)
        await env.upload_dir(workspace, "/workspace")
        restored = output / "verifier-input"
        restored.mkdir()
        await env.download_dir("/workspace", restored)
        digest = tree_digest(workspace)
        if tree_digest(restored) != digest:
            raise ValueError("Verifier input digest mismatch")
        container = main_container(env)
        rewards = await verify(task, env)
        return rewards, {
            "isolated": True,
            "workspace_digest": digest,
            "container": container,
            "verifier_digest": tree_digest(task.paths.tests_dir),
        }
    finally:
        ids = sandbox.project_resources(sandbox.session(row) + "-verify")
        if ids["containers"]:
            _docker("rm", "-f", *ids["containers"])
        for network in ids["networks"]:
            _docker("network", "rm", network)
