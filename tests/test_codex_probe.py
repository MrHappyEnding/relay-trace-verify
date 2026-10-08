"""Protocol fault injection; these mocked tests make no real-model claims."""

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from rtv import codex_probe as probe
from rtv.codex_agent import CODEX_VERSION, TurnResult
from rtv.harbor_probe import FINAL_SOURCE, PARTIAL_SOURCE

TASK = Path(__file__).resolve().parents[1] / "examples/tasks/codex-session"


@pytest.fixture
def protocol(monkeypatch, tmp_path):
    if sys.version_info < (3, 12):
        pytest.skip("Optional Harbor schema needs Python 3.12+")
    pytest.importorskip("harbor.models.task.task")
    state = {
        "envs": [],
        "failure": None,
        "calls": [],
        "source_id": str(uuid4()),
        "delete_flags": [],
    }

    class Environment:
        def __init__(self, role):
            self.session_id = role
            self.role = role
            self.root = tmp_path / "sandboxes" / role
            self.root.mkdir(parents=True)
            self.stopped = False
            self.deleted = False

        async def start(self, **kwargs):
            (self.root / "positive_sum.py").write_text(PARTIAL_SOURCE)

        async def stop(self, **kwargs):
            state["delete_flags"].append(kwargs["delete"])
            self.deleted = state["failure"] != "leak"

        async def download_dir(self, remote, local):
            if self.role == "source":
                assert self.stopped, "Snapshot must follow confirmed container stop"
            shutil.copytree(self.root, local, dirs_exist_ok=True)

        async def upload_dir(self, local, remote):
            shutil.copytree(local, self.root, dirs_exist_ok=True)
            if state["failure"] == "corrupt":
                (self.root / "positive_sum.py").write_text("corruption")

    def make_environment(task, output, role, image_id=None):
        env = Environment(role)
        state["envs"].append(env)
        return env

    class Adapter:
        def __init__(self, **kwargs):
            pass

        async def execute(self, env, prompt, *, native=None):
            state["calls"].append(env.role)
            if state["failure"] == "timeout":
                raise TimeoutError("injected timeout")
            if state["failure"] == "cancel":
                raise asyncio.CancelledError()
            identity = state["source_id"] if env.role != "handoff" else str(uuid4())
            if env.role == "source":
                state["marker"] = re.search(r"rtv-memory-[a-f0-9]+", prompt)[0]
            else:
                if env.role == "recovery":
                    assert native is not None
                    assert state["marker"] not in prompt  # Must recover native history.
                else:
                    assert native is None
                    assert state["marker"] in prompt
                (env.root / "positive_sum.py").write_text(FINAL_SOURCE)
                (env.root / "handoff_marker.txt").write_text(state["marker"])
            path = tmp_path / f"rollout-2026-10-08T00-00-00-{identity}.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": identity,
                            "cwd": "/workspace",
                            "cli_version": CODEX_VERSION,
                        },
                    }
                )
                + "\n"
            )
            return TurnResult(identity, path, {"input_tokens": 1, "output_tokens": 1})

    async def command(env, program):
        if program == "codex --version":
            return f"codex-cli {CODEX_VERSION}"
        if program.startswith("cat /workspace/"):
            name = program.split("/")[-1]
            if name == "quiescence.txt" and env.role == "source":
                (env.root / name).write_text(uuid4().hex)
            return (env.root / name).read_text()
        if program.startswith("rm -rf"):
            shutil.rmtree(env.root)
            env.root.mkdir()
        return ""

    async def verify(task, env):
        return {"reward": float(env.role != "source")}

    def docker(*args, **kwargs):
        if args[:2] == ("image", "tag"):
            state["retained_image"] = args[-1]
            return ""
        if args[:2] == ("image", "inspect"):
            if state["failure"] == "missing_image":
                raise RuntimeError("Retained image disappeared")
            return "[]"
        if args[0] == "stop":
            env = next(e for e in state["envs"] if e.role == args[-1])
            env.stopped = state["failure"] != "stop"
            return ""
        if args[2] == "{{.Image}}":
            return "sha256:fixture"
        env = next(e for e in state["envs"] if e.role == args[-1])
        return json.dumps({"Running": not env.stopped, "Restarting": False})

    monkeypatch.setattr(probe, "doctor", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(probe, "make_environment", make_environment)
    monkeypatch.setattr(probe, "HarborCodexAdapter", Adapter)
    monkeypatch.setattr(probe, "command", command)
    monkeypatch.setattr(probe, "verify", verify)
    monkeypatch.setattr(probe, "_docker", docker)
    monkeypatch.setattr(probe, "main_container", lambda env: env.role)
    from rtv import codex_lifecycle

    async def stop_sandbox(env):
        docker("stop", "--time", "2", env.role)
        if not env.stopped:
            raise RuntimeError("Sandbox still running")

    monkeypatch.setattr(codex_lifecycle, "stop_sandbox", stop_sandbox)
    monkeypatch.setattr(
        probe,
        "_container_ids",
        lambda role: [e.role for e in state["envs"] if e.role == role and not e.deleted],
    )
    return state


def run(tmp_path):
    return asyncio.run(
        probe.run_probe(
            TASK,
            tmp_path / "run",
            model="fixture-model",
            auth_file=tmp_path / "auth.json",
            allow_model_calls=True,
        )
    )


def test_recovery_preserves_attempt_and_handoff_changes_session(protocol, tmp_path):
    report = run(tmp_path)
    assert report["status"] == "passed"
    turns = report["turns"]
    assert (
        turns["recovery"]["execution"]["attempt_id"] == turns["source"]["execution"]["attempt_id"]
    )
    assert turns["recovery"]["execution"]["epoch"] == 2
    assert turns["handoff"]["execution"]["attempt_id"] != turns["source"]["execution"]["attempt_id"]
    assert protocol["calls"] == ["source", "recovery", "handoff"]
    assert all(e.deleted for e in protocol["envs"])
    assert report["image_ref"] == protocol["retained_image"]
    assert protocol["delete_flags"]
    assert not any(protocol["delete_flags"]), "Checkpoint cleanup must not delete source images"


@pytest.mark.parametrize(
    "failure", ["stop", "timeout", "cancel", "corrupt", "leak", "missing_image"]
)
def test_failure_refuses_unsafe_progress_and_cleans_up(protocol, tmp_path, failure):
    protocol["failure"] = failure
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            run(tmp_path)
        report = json.loads((tmp_path / "run/report.json").read_text())
        assert report["status"] == "interrupted"
    else:
        report = run(tmp_path)
        assert report["status"] == "failed"
    if failure in {"stop", "timeout", "cancel"}:
        assert "checkpoint" not in report
    if failure not in {"leak", "missing_image"}:
        assert all(e.deleted for e in protocol["envs"])
        assert protocol["calls"] == ["source"]
    else:
        assert report["cleanup_errors"]
