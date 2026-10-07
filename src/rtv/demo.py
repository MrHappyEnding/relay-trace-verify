"""Deterministic fixtures, not LLM agents. No subprocesses, API calls, or credentials."""

import json
from dataclasses import dataclass
from pathlib import Path

from rtv.domain import (
    AdapterCapabilities,
    AgentRequest,
    AgentResult,
    AgentStatus,
    VerificationResult,
)
from rtv.events import JsonlEventSink
from rtv.runner import RelayReport, run_two_stage_relay
from rtv.storage import LocalCheckpointStore, LocalWorkspaceBackend


class PrepareAgent:
    name = "fixture-prepare"
    capabilities = AdapterCapabilities(stage_checkpoint=True)

    def run(self, request: AgentRequest) -> AgentResult:
        (request.workspace / "answer.txt").write_text("41\n", encoding="utf-8")
        return AgentResult(
            AgentStatus.NEEDS_HANDOFF,
            "answer.txt contains 41; the next stage must change it to 42.",
        )


class FinishAgent:
    name = "fixture-finish"
    capabilities = AdapterCapabilities(stage_checkpoint=True)

    def run(self, request: AgentRequest) -> AgentResult:
        if request.handoff is None:
            raise ValueError("Fixture finish agent requires a handoff context")
        if (request.workspace / "answer.txt").read_text(encoding="utf-8") != "41\n":
            raise ValueError("Expected the source agent's restored artifact")
        (request.workspace / "answer.txt").write_text("42\n", encoding="utf-8")
        return AgentResult(AgentStatus.COMPLETED, "Updated the restored answer.txt to 42.")


@dataclass(frozen=True)
class FixtureVerifier:
    expected: str = "42\n"

    def verify(self, workspace: Path) -> VerificationResult:
        try:
            actual = (workspace / "answer.txt").read_text(encoding="utf-8")
        except FileNotFoundError:
            return VerificationResult(False, "answer.txt is missing")
        passed = actual == self.expected
        return VerificationResult(
            passed, "Expected content matches" if passed else "Content mismatch"
        )


def run_demo(output: Path) -> RelayReport:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    checkpoints = LocalCheckpointStore(output / "checkpoints")
    workspaces = LocalWorkspaceBackend(output / "workspaces", checkpoints)
    events = JsonlEventSink(output / "events.jsonl")
    report = run_two_stage_relay(
        task="Write answer.txt containing 42 followed by a newline.",
        first=PrepareAgent(),
        second=FinishAgent(),
        workspaces=workspaces,
        checkpoints=checkpoints,
        events=events,
        verifier=FixtureVerifier(),
    )
    (output / "report.json").write_text(
        json.dumps(report.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    return report
