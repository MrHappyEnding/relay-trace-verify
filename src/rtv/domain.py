"""Domain identities and explicit state transitions, independent of orchestration vendors."""

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class TrialStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


_TRANSITIONS = {
    TrialStatus.QUEUED: {TrialStatus.RUNNING, TrialStatus.CANCEL_REQUESTED},
    TrialStatus.RUNNING: {
        TrialStatus.VERIFYING,
        TrialStatus.FAILED,
        TrialStatus.CANCEL_REQUESTED,
    },
    TrialStatus.VERIFYING: {
        TrialStatus.SUCCEEDED,
        TrialStatus.FAILED,
        TrialStatus.CANCEL_REQUESTED,
    },
    TrialStatus.CANCEL_REQUESTED: {TrialStatus.CANCELLED},
    TrialStatus.SUCCEEDED: set(),
    TrialStatus.FAILED: set(),
    TrialStatus.CANCELLED: set(),
}


@dataclass(frozen=True)
class Trial:
    task: str
    trial_id: UUID = field(default_factory=uuid4)
    status: TrialStatus = TrialStatus.QUEUED

    def transition(self, target: TrialStatus) -> "Trial":
        if target == self.status:
            return self
        if target not in _TRANSITIONS[self.status]:
            raise ValueError(f"Invalid trial transition: {self.status} -> {target}")
        return replace(self, status=target)


@dataclass(frozen=True)
class ExecutionRef:
    trial_id: UUID
    attempt_id: UUID = field(default_factory=uuid4)
    execution_id: UUID = field(default_factory=uuid4)
    epoch: int = 1

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, UUID) for value in (self.trial_id, self.attempt_id, self.execution_id)
        ):
            raise ValueError("Execution identities must be UUID objects")
        if type(self.epoch) is not int or self.epoch < 1:
            raise ValueError("Execution epoch must be a positive integer")

    def recover(self) -> "ExecutionRef":
        """Create a proposed successor identity; durable ownership is not implemented here."""
        return replace(self, execution_id=uuid4(), epoch=self.epoch + 1)

    def handoff(self) -> "ExecutionRef":
        return ExecutionRef(trial_id=self.trial_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": str(self.trial_id),
            "attempt_id": str(self.attempt_id),
            "execution_id": str(self.execution_id),
            "epoch": self.epoch,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExecutionRef":
        return cls(
            trial_id=UUID(value["trial_id"]),
            attempt_id=UUID(value["attempt_id"]),
            execution_id=UUID(value["execution_id"]),
            epoch=value["epoch"],
        )


@dataclass(frozen=True)
class AdapterCapabilities:
    stage_checkpoint: bool = False
    native_session_import: bool = False
    portable_trajectory_import: bool = False
    strict_budget_admission: bool = False


class AgentStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_HANDOFF = "needs_handoff"


@dataclass(frozen=True)
class HandoffContext:
    checkpoint_id: UUID
    summary: str


@dataclass(frozen=True)
class AgentRequest:
    task: str
    execution: ExecutionRef
    workspace: Path
    handoff: HandoffContext | None = None


@dataclass(frozen=True)
class AgentResult:
    status: AgentStatus
    summary: str


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    detail: str
