"""Local two-stage relay composition; Temporal and durable ownership are future integrations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtv.checkpoints import CheckpointRef
from rtv.contracts import AgentAdapter, CheckpointStore, EventSink, Verifier, WorkspaceBackend
from rtv.domain import (
    AgentRequest,
    AgentStatus,
    ExecutionRef,
    HandoffContext,
    Trial,
    TrialStatus,
    VerificationResult,
)


@dataclass(frozen=True)
class RelayReport:
    trial: Trial
    checkpoint: CheckpointRef
    source: ExecutionRef
    target: ExecutionRef
    source_workspace: Path
    target_workspace: Path
    verification: VerificationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": "local-fixture",
            "trial_id": str(self.trial.trial_id),
            "status": self.trial.status.value,
            "checkpoint": self.checkpoint.to_dict(),
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "source_workspace": str(self.source_workspace),
            "target_workspace": str(self.target_workspace),
            "verification": {
                "passed": self.verification.passed,
                "detail": self.verification.detail,
            },
        }


def run_two_stage_relay(
    *,
    task: str,
    first: AgentAdapter,
    second: AgentAdapter,
    workspaces: WorkspaceBackend,
    checkpoints: CheckpointStore,
    events: EventSink,
    verifier: Verifier,
) -> RelayReport:
    if not first.capabilities.stage_checkpoint:
        raise ValueError(f"Adapter {first.name!r} cannot establish a checkpoint boundary")
    trial = Trial(task=task).transition(TrialStatus.RUNNING)
    source = ExecutionRef(trial_id=trial.trial_id)
    source_workspace = workspaces.prepare(source)
    events.emit("trial.started", source, {"task": task, "agent": first.name})
    first_result = first.run(AgentRequest(task, source, source_workspace))
    if first_result.status != AgentStatus.NEEDS_HANDOFF:
        raise ValueError("Two-stage relay requires the first agent to request a handoff")
    cursor = events.emit(
        "agent.stage_completed",
        source,
        {"agent": first.name, "status": first_result.status, "summary": first_result.summary},
    )
    # The adapter contract requires all writes to have stopped before this snapshot.
    reference = checkpoints.commit(
        source_workspace,
        source,
        event_cursor=cursor,
        summary=first_result.summary,
    )
    events.emit("checkpoint.committed", source, reference.to_dict())
    manifest = checkpoints.load(reference)
    target = source.handoff()
    target_workspace = workspaces.prepare(target, reference)
    events.emit(
        "handoff.started",
        target,
        {"source_execution_id": str(source.execution_id), **reference.to_dict()},
    )
    result = second.run(
        AgentRequest(
            task,
            target,
            target_workspace,
            HandoffContext(reference.checkpoint_id, manifest.summary),
        )
    )
    events.emit(
        "agent.stage_completed",
        target,
        {"agent": second.name, "status": result.status, "summary": result.summary},
    )
    if result.status != AgentStatus.COMPLETED:
        raise ValueError("Two-stage relay requires the second agent to finish its stage")
    trial = trial.transition(TrialStatus.VERIFYING)
    verification = verifier.verify(target_workspace)
    events.emit(
        "verification.completed",
        target,
        {"passed": verification.passed, "detail": verification.detail},
    )
    status = TrialStatus.SUCCEEDED if verification.passed else TrialStatus.FAILED
    trial = trial.transition(status)
    events.emit("trial.completed", target, {"status": trial.status})
    return RelayReport(
        trial,
        reference,
        source,
        target,
        source_workspace,
        target_workspace,
        verification,
    )
