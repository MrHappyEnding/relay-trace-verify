"""Local service configuration; credentials never enter Workflow history."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TERMINAL = {"succeeded", "failed", "cancelled"}
LEASE_SECONDS = 20


def execution_dir(settings, row):
    return settings.root / row["trial_id"] / "executions" / row["execution_id"]


def task_path(adapter: str) -> Path:
    return (
        REPO
        / "examples/tasks"
        / ("codex-session" if adapter in {"codex", "codex-session"} else "positive-sum")
    )


@dataclass(frozen=True)
class Settings:
    root: Path
    dsn: str
    temporal: str

    @classmethod
    def load(cls):
        return cls(
            Path(os.environ.get("RTV_RUNTIME_ROOT", REPO / "work/runtime-v3")).resolve(),
            os.environ.get("RTV_DATABASE_URL", "postgresql://rtv:rtv-local@127.0.0.1:55432/rtv"),
            os.environ.get("RTV_TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        )

    @property
    def queue(self):
        # Local artifacts cannot be taken over by a worker on a different host/root.
        return "rtv-m3-" + hashlib.sha256(str(self.root).encode()).hexdigest()[:16]
