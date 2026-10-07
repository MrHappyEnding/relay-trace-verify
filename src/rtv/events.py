"""Append-only JSONL events for one local runner. Reopening existing logs is unsupported."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rtv.domain import ExecutionRef


class JsonlEventSink:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8"):
            pass
        self.path = path
        self.sequence = 0

    def emit(self, kind: str, execution: ExecutionRef, payload: dict[str, Any]) -> int:
        sequence = self.sequence + 1
        event = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "sequence": sequence,
            "recorded_at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "execution": execution.to_dict(),
            "payload": payload,
        }
        # Serialize before opening the log, so invalid payloads cannot append partial JSON.
        line = json.dumps(event, sort_keys=True, allow_nan=False) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self.sequence = sequence
        return sequence
