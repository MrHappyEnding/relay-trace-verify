import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from rtv import codex_boundaries as probe
from rtv.capabilities import capability_report
from rtv.codex_agent import CODEX_VERSION
from rtv.domain import ExecutionRef
from rtv.harbor_probe import tree_digest
from rtv.storage import LocalCheckpointStore

TASK = Path(__file__).resolve().parents[1] / "examples/tasks/codex-session"


def test_source_image_must_survive_checkpoint_cleanup(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "fixture").write_text("committed")
    source = tmp_path / "source"
    store = LocalCheckpointStore(source / "checkpoints")
    reference = store.commit(workspace, ExecutionRef(uuid4()), event_cursor=1, summary="fixture")
    (source / "report.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "mode": "codex-checkpoint",
                "task_digest": tree_digest(TASK),
                "model": "fixture",
                "cli_version": CODEX_VERSION,
                "image_id": "sha256:fixture",
                "checkpoint": reference.to_dict(),
            }
        )
    )
    calls = []

    def missing(*args):
        calls.append(args)
        raise RuntimeError("image unavailable")

    monkeypatch.setattr(probe, "_docker", missing)
    with pytest.raises(ValueError, match="will not silently rebuild"):
        probe.load_source(source, TASK, "fixture")
    assert calls == [("image", "inspect", "sha256:fixture")]


def test_boundary_probe_requires_explicit_model_opt_in(tmp_path):
    with pytest.raises(ValueError, match="allow-model-calls"):
        asyncio.run(
            probe.run_boundaries(
                TASK, tmp_path / "source", tmp_path / "output", model="fixture", auth_file=None
            )
        )
    assert not (tmp_path / "output").exists()


def test_capabilities_do_not_overclaim_budget_or_process_recovery():
    report = capability_report()
    assert report["codex"]["capabilities"]["portable_trajectory_import"]
    assert not report["codex"]["capabilities"]["strict_budget_admission"]
    assert "UNKNOWN" in report["codex"]["budget_scope"]
    assert "mid-tool continuation" in report["not_verified"]
    assert "symbolic links" in report["checkpoint_policy"]["reject"]
