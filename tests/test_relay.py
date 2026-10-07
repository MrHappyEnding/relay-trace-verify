import json
from uuid import uuid4

import pytest

from rtv.cli import main
from rtv.demo import FinishAgent, FixtureVerifier, PrepareAgent, run_demo
from rtv.domain import AdapterCapabilities, ExecutionRef, TrialStatus
from rtv.events import JsonlEventSink
from rtv.runner import run_two_stage_relay
from rtv.storage import LocalCheckpointStore, LocalWorkspaceBackend


def test_demo_restores_into_independent_workspace_and_correlates_events(tmp_path):
    output = tmp_path / "demo"
    report = run_demo(output)
    assert report.trial.status == TrialStatus.SUCCEEDED
    assert report.source.attempt_id != report.target.attempt_id
    assert report.source.trial_id == report.target.trial_id
    assert (report.source_workspace / "answer.txt").read_text() == "41\n"
    assert (report.target_workspace / "answer.txt").read_text() == "42\n"
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
    assert [event["sequence"] for event in events] == list(range(1, 8))
    assert events[2]["kind"] == "checkpoint.committed"
    assert events[3]["payload"]["source_execution_id"] == str(report.source.execution_id)
    assert events[-1]["payload"]["status"] == "succeeded"
    assert json.loads((output / "report.json").read_text())["verification"]["passed"]


def test_verification_failure_prevents_success(tmp_path):
    checkpoints = LocalCheckpointStore(tmp_path / "store")
    report = run_two_stage_relay(
        task="fixture",
        first=PrepareAgent(),
        second=FinishAgent(),
        workspaces=LocalWorkspaceBackend(tmp_path / "workspaces", checkpoints),
        checkpoints=checkpoints,
        events=JsonlEventSink(tmp_path / "events.jsonl"),
        verifier=FixtureVerifier(expected="different\n"),
    )
    assert report.trial.status == TrialStatus.FAILED
    assert not report.verification.passed


def test_adapter_without_checkpoint_boundary_is_rejected(tmp_path):
    first = PrepareAgent()
    first.capabilities = AdapterCapabilities()
    checkpoints = LocalCheckpointStore(tmp_path / "store")
    with pytest.raises(ValueError, match="cannot establish a checkpoint boundary"):
        run_two_stage_relay(
            task="fixture",
            first=first,
            second=FinishAgent(),
            workspaces=LocalWorkspaceBackend(tmp_path / "workspaces", checkpoints),
            checkpoints=checkpoints,
            events=JsonlEventSink(tmp_path / "events.jsonl"),
            verifier=FixtureVerifier(),
        )
    assert not list((tmp_path / "workspaces").iterdir())


def test_event_log_does_not_overwrite_existing_events(tmp_path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)
    sink.emit("first", ExecutionRef(uuid4()), {})
    with pytest.raises(FileExistsError):
        JsonlEventSink(path)
    assert len(path.read_text().splitlines()) == 1


def test_invalid_event_payload_does_not_consume_a_sequence(tmp_path):
    sink = JsonlEventSink(tmp_path / "events.jsonl")
    execution = ExecutionRef(uuid4())
    with pytest.raises(ValueError):
        sink.emit("invalid", execution, {"value": float("nan")})
    assert sink.emit("valid", execution, {}) == 1


def test_cli_emits_report_and_refuses_existing_output(tmp_path, capsys):
    output = str(tmp_path / "demo")
    assert main(["demo", "--output", output]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
    assert main(["demo", "--output", output]) == 1
    assert "rtv:" in capsys.readouterr().err
