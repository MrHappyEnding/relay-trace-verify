import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rtv.cli import main
from rtv.codex_agent import (
    CODEX_VERSION,
    HarborCodexAdapter,
    incremental_usage,
    read_turn,
    validate_bundle,
)
from rtv.codex_probe import command, run_probe


def session_file(root, session_id=None):
    identity = session_id or str(uuid4())
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"rollout-2026-10-08T00-00-00-{identity}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": identity,
                    "cli_version": CODEX_VERSION,
                    "cwd": "/workspace",
                },
            }
        )
        + "\n"
    )
    return path, identity


def turn_logs(root):
    path, identity = session_file(root / "sessions")
    events = [
        {"type": "thread.started", "thread_id": identity},
        {"type": "turn.completed", "usage": {"input_tokens": 30, "output_tokens": 4}},
    ]
    (root / "codex.txt").write_text("CLI notice\n" + "\n".join(map(json.dumps, events)))
    return path, identity, events


def test_completed_turn_requires_matching_native_identity(tmp_path):
    path, identity, _ = turn_logs(tmp_path)
    result = read_turn(tmp_path)
    assert result.session_id == identity
    assert result.rollout == path
    assert result.usage["input_tokens"] == 30
    data = json.loads(path.read_text())
    data["payload"]["id"] = str(uuid4())
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identities disagree"):
        read_turn(tmp_path)


@pytest.mark.parametrize("failure", ["incomplete", "failed", "duplicate", "wrong-version"])
def test_pipeline_success_does_not_imply_recoverable_turn(tmp_path, failure):
    path, _, events = turn_logs(tmp_path)
    if failure == "incomplete":
        events.pop()
    elif failure == "failed":
        events.append({"type": "turn.failed"})
    elif failure == "duplicate":
        session_file(tmp_path / "sessions")
    else:
        path.write_text(path.read_text().replace(CODEX_VERSION, "0.0.0"))
    (tmp_path / "codex.txt").write_text("\n".join(map(json.dumps, events)))
    with pytest.raises(ValueError):
        read_turn(tmp_path)


def test_bundle_refuses_cross_model_and_missing_native_state(tmp_path):
    path, identity = session_file(tmp_path / "native")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cli_version": CODEX_VERSION,
                "session_id": identity,
                "model": "fixture-model",
            }
        )
    )
    assert validate_bundle(tmp_path, model="fixture-model") == path
    with pytest.raises(ValueError, match="original model"):
        validate_bundle(tmp_path, model="other-model")
    path.unlink()
    with pytest.raises(ValueError, match="exactly one"):
        validate_bundle(tmp_path, model="fixture-model")


def test_auth_rejects_api_fallback_and_model_shell_syntax(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"OPENAI_API_KEY": "test-placeholder"}))
    with pytest.raises(ValueError, match="subscription auth"):
        HarborCodexAdapter(model="model", auth_file=auth)
    auth.write_text(json.dumps({"tokens": {"access_token": "fixture"}}))
    assert HarborCodexAdapter(model="fixture-model", auth_file=auth).model == "fixture-model"
    with pytest.raises(ValueError, match="plain model"):
        HarborCodexAdapter(model="model; bad", auth_file=auth)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid")
    with pytest.raises(ValueError, match="Custom API endpoints"):
        HarborCodexAdapter(model="fixture-model", auth_file=auth)


def test_real_model_usage_requires_opt_in_before_side_effects(tmp_path, capsys):
    output = tmp_path / "run"
    assert main(["codex-smoke", "--output", str(output)]) == 1
    assert "--allow-model-calls" in capsys.readouterr().err
    assert not output.exists()


def test_invalid_timeout_fails_before_side_effects(tmp_path):
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="timeout"):
        asyncio.run(
            run_probe(Path("missing"), output, model="fixture", prepare_only=True, turn_timeout=0)
        )
    assert not output.exists()


def test_container_command_can_succeed_without_stdout():
    class Environment:
        async def exec(self, **kwargs):
            return SimpleNamespace(return_code=0, stdout=None)

    assert asyncio.run(command(Environment(), "fixture-no-output")) == ""


def test_resumed_usage_subtracts_imported_prefix_and_keeps_uncertainty(tmp_path):
    _, identity, events = turn_logs(tmp_path)
    native, _ = session_file(tmp_path / "prior", identity)
    native.write_text(
        native.read_text()
        + json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 20,
                            "output_tokens": 3,
                        }
                    },
                },
            }
        )
        + "\n"
    )
    result = read_turn(tmp_path, native=native)
    assert result.session_usage == events[-1]["usage"]
    assert result.usage == {"input_tokens": 10, "output_tokens": 1}
    assert incremental_usage({"input_tokens": 10}, {"input_tokens": 20}) is None
    assert incremental_usage({"input_tokens": 10}, None) is None


@pytest.mark.parametrize("gate_version", ["codex-tools-v1", "codex-tools-v2"])
def test_adapter_installs_only_vetted_gate_and_audits_receipts(tmp_path, monkeypatch, gate_version):
    harbor = pytest.importorskip("harbor.agents.installed.codex")
    from rtv import codex_gate

    calls, uploads = {}, []
    logs = tmp_path / "logs"
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "fixture"}}))

    class FakeCodex:
        def __init__(self, **kwargs):
            calls.update(kwargs)

        async def run(self, instruction, env, context):
            calls["prompt"] = instruction
            path, sid, _ = turn_logs(logs)
            path.write_text(path.read_text().replace('"/workspace"', '"/testbed"'))
            # Natural early completion must still yield a valid boundary, including zero tools.
            for event in ("SessionStart", "Stop"):
                codex_gate.handle(
                    logs / "tool-gate.json",
                    {
                        "hook_event_name": event,
                        "session_id": sid,
                    },
                )

    class Environment:
        trial_paths = SimpleNamespace(agent_dir=logs)

        async def exec(self, command, **kwargs):
            return SimpleNamespace(
                return_code=0,
                stdout=(f"codex-cli {CODEX_VERSION}" if command == "codex --version" else "[]"),
            )

        async def upload_file(self, source, target):
            uploads.append((source, target))

        async def download_file(self, source, target):
            assert source == codex_gate.REMOTE_STATE and target == logs / "tool-gate.json"

    monkeypatch.setattr(harbor, "Codex", FakeCodex)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    adapter = HarborCodexAdapter(model="fixture", auth_file=auth, workspace="/testbed")
    turn = asyncio.run(
        adapter.execute(
            Environment(),
            "public task",
            tool_prefix={"version": gate_version, "max_tools": 6},
        )
    )
    assert calls["config"]["hooks"] == codex_gate.hook_config()
    assert calls["dangerously_bypass_hook_trust"] is True
    assert uploads[0] == (Path(codex_gate.__file__), codex_gate.REMOTE_SCRIPT)
    assert turn.prefix_boundary["trigger"] == "natural_completion"
    if gate_version == codex_gate.VERSION:
        assert "at most 6" in calls["prompt"]
    else:
        assert "at most" not in calls["prompt"] and "6" not in calls["prompt"]
        assert "When a tool hook requests handoff" in calls["prompt"]
