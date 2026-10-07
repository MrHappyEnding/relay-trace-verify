"""Small, synchronous stage-level contracts; real Harbor integration remains an M0 task."""

from pathlib import Path
from typing import Any, Protocol

from rtv.checkpoints import CheckpointManifest, CheckpointRef
from rtv.domain import (
    AdapterCapabilities,
    AgentRequest,
    AgentResult,
    ExecutionRef,
    VerificationResult,
)


class AgentAdapter(Protocol):
    name: str
    capabilities: AdapterCapabilities

    def run(self, request: AgentRequest) -> AgentResult:
        """Complete one stage; stage_checkpoint promises no writes remain after return."""
        ...


class CheckpointStore(Protocol):
    def commit(
        self, workspace: Path, execution: ExecutionRef, *, event_cursor: int, summary: str
    ) -> CheckpointRef: ...

    def load(self, reference: CheckpointRef) -> CheckpointManifest: ...

    def restore(self, reference: CheckpointRef, destination: Path) -> Path: ...


class WorkspaceBackend(Protocol):
    def prepare(self, execution: ExecutionRef, checkpoint: CheckpointRef | None = None) -> Path: ...


class EventSink(Protocol):
    def emit(self, kind: str, execution: ExecutionRef, payload: dict[str, Any]) -> int:
        """Return the persisted, source-local event sequence number."""
        ...


class Verifier(Protocol):
    def verify(self, workspace: Path) -> VerificationResult: ...
