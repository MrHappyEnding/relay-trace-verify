"""Named, queryable Docker resources owned by one local RTV Execution."""

import asyncio

from rtv.harbor_probe import _container_ids, _docker


def bounded_environment_type(*, startup_timeout_seconds=60, exec_timeout_seconds=60):
    from harbor.environments.docker.docker import DockerEnvironment

    if not 30 <= startup_timeout_seconds <= 300:
        raise ValueError("Environment startup timeout must be 30–300 seconds")
    if type(exec_timeout_seconds) is not int or not 1 <= exec_timeout_seconds <= 1830:
        raise ValueError("Environment exec timeout must be 1–1830 seconds")

    class BoundedDockerEnvironment(DockerEnvironment):
        # Pinned Harbor 0.24.0 has no public default command timeout. Its timeout
        # handler terminates and drains Compose; asyncio cancellation does not.
        async def _run_docker_compose_command(
            self, command, check=True, timeout_sec=None, stdin_data=None, on_output=None
        ):
            # Harbor runs the entire Codex CLI through a single exec without a
            # timeout. Give that command the stage envelope; explicit tool limits
            # and other lifecycle commands keep their own bounds.
            fallback = {
                "up": startup_timeout_seconds,
                "exec": exec_timeout_seconds,
            }.get(command[0], 60)
            return await super()._run_docker_compose_command(
                command,
                check=check,
                timeout_sec=timeout_sec or fallback,
                stdin_data=stdin_data,
                on_output=on_output,
            )

        @staticmethod
        async def _collect_buffered_output(process, **kwargs):
            try:
                return await DockerEnvironment._collect_buffered_output(process, **kwargs)
            except asyncio.CancelledError:
                await DockerEnvironment._terminate_process(process)
                raise

        @staticmethod
        async def _collect_streamed_output(process, **kwargs):
            try:
                return await DockerEnvironment._collect_streamed_output(process, **kwargs)
            except asyncio.CancelledError:
                await DockerEnvironment._terminate_process(process)
                raise

    return BoundedDockerEnvironment


def session(row: dict) -> str:
    return "rtv-m2-" + row["execution_id"].replace("-", "")


def project_resources(project: str) -> dict:
    return {
        "containers": _container_ids(project),
        "networks": _docker(
            "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"
        ).split(),
    }


def resources(row: dict) -> dict:
    groups = [project_resources(session(row)), project_resources(session(row) + "-verify")]
    return {k: [v for group in groups for v in group[k]] for k in ("containers", "networks")}


def stop(row: dict):
    ids = resources(row)["containers"]
    if ids:
        _docker("stop", "--time", "2", *ids)


def cleanup(row: dict):
    """Retryable cleanup of this exact project; never prune images/user resources."""
    found = resources(row)
    if found["containers"]:
        _docker("rm", "-f", *found["containers"])
    for network in found["networks"]:
        _docker("network", "rm", network)
    if any(resources(row).values()):
        raise RuntimeError("RTV execution resources remain; cleanup must retry")
