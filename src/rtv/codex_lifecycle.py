"""Sandbox ownership for a single local turn; no distributed lease is implied."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from rtv.codex_probe import main_container
from rtv.harbor_probe import _docker


async def stop_sandbox(env: Any) -> None:
    container = main_container(env)
    # Stop the actual tools before cancelling the docker-exec client and its SDK cleanup.
    await asyncio.to_thread(_docker, "stop", "--time", "2", container)
    state = json.loads(
        await asyncio.to_thread(_docker, "inspect", "--format", "{{json .State}}", container)
    )
    if state["Running"] or state["Restarting"] or state.get("Pid", 0):
        raise RuntimeError("Sandbox still has running processes; quiescence not established")


async def owned_turn(
    execute: Callable[[], Awaitable[Any]],
    env: Any,
    *,
    timeout: float,
    stop: Callable[[Any], Awaitable[None]] | None = None,
) -> Any:
    """Stop the entire sandbox before draining a cancelled/failed transport task.

    asyncio.wait_for alone waits for SDK cancellation cleanup, which may still
    issue docker exec commands while the old agent/tool process keeps running.
    """
    task = asyncio.create_task(execute())
    stop = stop or stop_sandbox
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if not done:
            raise TimeoutError("Agent turn exceeded its wall-clock allowance")
        return task.result()
    except BaseException:
        try:
            await stop(env)
        finally:
            task.cancel()
            # Retrieve errors from a finished task as well as a cancelled one.
            try:
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=10)
            except TimeoutError:
                pass
        raise
