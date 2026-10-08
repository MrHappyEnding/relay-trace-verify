import asyncio

import pytest

from rtv.codex_lifecycle import owned_turn


@pytest.mark.parametrize("failure", ["timeout", "cancel", "transport", "stop"])
def test_stop_precedes_transport_cancellation_and_errors_propagate(failure):
    events = []

    async def scenario():
        started = asyncio.Event()

        async def execute():
            started.set()
            if failure == "transport":
                raise ValueError("transport failed")
            try:
                await asyncio.Event().wait()
            finally:
                events.append("transport-cancelled")

        async def stop(env):
            events.append("sandbox-stopped")
            if failure == "stop":
                raise RuntimeError("stop failed")

        task = asyncio.create_task(owned_turn(execute, object(), timeout=0.02, stop=stop))
        await started.wait()
        if failure == "cancel":
            task.cancel()
        error = {
            "timeout": TimeoutError,
            "cancel": asyncio.CancelledError,
            "transport": ValueError,
            "stop": RuntimeError,
        }[failure]
        with pytest.raises(error):
            await task

    asyncio.run(scenario())
    assert events[0] == "sandbox-stopped"
    if failure != "transport":
        assert events == ["sandbox-stopped", "transport-cancelled"]


def test_completed_turn_keeps_environment_available_for_snapshot():
    async def execute():
        return "complete"

    async def stop(env):
        pytest.fail("Successful turn should not force container stop")

    assert asyncio.run(owned_turn(execute, object(), timeout=1, stop=stop)) == "complete"
