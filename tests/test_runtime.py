"""Local invariants; no services or model calls required."""

import asyncio
import json
import sys
from argparse import Namespace

import pytest

from rtv.runtime.cli import make_spec
from rtv.runtime.files import admission, atomic_json, lock


def test_local_locks_exclude_other_owners_and_release(tmp_path):
    path = tmp_path / "execution.lock"
    with lock(path) as first:
        assert first
        with lock(path) as second:
            assert not second
    with lock(path) as after_exit:
        assert after_exit


def test_capacity_is_held_for_execution_lifetime(tmp_path):
    with admission(tmp_path, 2) as first, admission(tmp_path, 2) as second:
        assert first and second
        with admission(tmp_path, 2) as third:
            assert not third
    with admission(tmp_path, 2) as released:
        assert released


def test_atomic_result_retains_previous_on_failed_write(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    atomic_json(path, {"status": "running"})

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("rtv.runtime.files.os.fsync", fail)
    with pytest.raises(OSError):
        atomic_json(path, {"status": "succeeded"})
    assert json.loads(path.read_text()) == {"status": "running"}


@pytest.mark.parametrize(
    "updates",
    [
        {"ttl": 0},
        {"ttl": 1801},
        {"delay": -1},
        {"delay": 601},
        {"adapter": "codex", "allow_model_calls": False},
        {"adapter": "codex", "allow_model_calls": True, "delay": 1},
    ],
)
def test_invalid_submission_rejected_before_docker(updates, monkeypatch):
    args = Namespace(ttl=300, delay=0, adapter="fixture", allow_model_calls=False)
    args.__dict__.update(updates)

    def docker_forbidden(*args):
        pytest.fail("Invalid submission must not touch Docker")

    monkeypatch.setattr("rtv.runtime.cli._docker", docker_forbidden)
    with pytest.raises(ValueError):
        make_spec(args)


def test_operation_identity_survives_retries_but_not_other_roots():
    pytest.importorskip("psycopg")
    from rtv.runtime.store import identity, spec_hash

    first = identity("/runtime/a", "stable-key")
    assert first == identity("/runtime/a", "stable-key")
    assert first != identity("/runtime/b", "stable-key")
    assert len(set(first.values())) == 4
    assert spec_hash({"a": 1, "b": 2}) == spec_hash({"b": 2, "a": 1})
    with pytest.raises(ValueError):
        identity("/runtime/a", " ")


def test_cancel_drains_compose_client_process():
    pytest.importorskip("harbor")
    from rtv.runtime.sandbox import bounded_environment_type

    async def scenario():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        task = asyncio.create_task(
            bounded_environment_type()._collect_buffered_output(process, timeout_sec=60)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None

    asyncio.run(scenario())
