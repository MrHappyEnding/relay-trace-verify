import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from rtv.checkpoints import CheckpointError, CheckpointRef
from rtv.domain import ExecutionRef
from rtv.storage import LocalCheckpointStore


@pytest.fixture
def snapshot(tmp_path):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "nested").mkdir()
    (workspace / "empty").mkdir()
    (workspace / "nested" / "run.sh").write_bytes(b"#!/bin/sh\nexit 0\n")
    (workspace / "nested" / "run.sh").chmod(0o755)
    (workspace / "new.bin").write_bytes(b"\x00\x01\xff")
    store = LocalCheckpointStore(tmp_path / "store")
    execution = ExecutionRef(trial_id=uuid4())
    reference = store.commit(workspace, execution, event_cursor=2, summary="stage complete")
    return store, reference, workspace


def test_restore_preserves_files_modes_empty_directories_and_lineage(snapshot, tmp_path):
    store, reference, source = snapshot
    destination = store.restore(reference, tmp_path / "restored")
    assert (destination / "new.bin").read_bytes() == b"\x00\x01\xff"
    assert (destination / "nested" / "run.sh").stat().st_mode & 0o777 == 0o755
    assert (destination / "empty").is_dir()
    manifest = store.load(reference)
    assert manifest.event_cursor == 2
    assert manifest.summary == "stage complete"
    assert manifest.checkpoint_id == reference.checkpoint_id
    (destination / "new.bin").write_bytes(b"changed")
    assert (source / "new.bin").read_bytes() == b"\x00\x01\xff"
    branch = store.restore(reference, tmp_path / "another-branch")
    assert (branch / "new.bin").read_bytes() == b"\x00\x01\xff"


def test_manifest_corruption_is_detected(snapshot):
    store, reference, _ = snapshot
    manifest = store.root / str(reference.checkpoint_id) / "manifest.json"
    manifest.write_text("{}")
    with pytest.raises(CheckpointError, match="Manifest digest mismatch"):
        store.load(reference)


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("directories", "nested")])
def test_unsupported_or_malformed_schema_is_rejected(snapshot, field, value):
    store, reference, _ = snapshot
    path = store.root / str(reference.checkpoint_id) / "manifest.json"
    content = json.loads(path.read_bytes())
    content[field] = value
    data = json.dumps(content).encode()
    path.write_bytes(data)
    altered = CheckpointRef(reference.checkpoint_id, hashlib.sha256(data).hexdigest())
    with pytest.raises(CheckpointError):
        store.load(altered)


def test_reference_rejects_path_like_identity():
    with pytest.raises(CheckpointError, match="UUID"):
        CheckpointRef("../outside", "0" * 64)


def test_missing_file_does_not_publish_restore(snapshot, tmp_path):
    store, reference, _ = snapshot
    (store.root / str(reference.checkpoint_id) / "files" / "new.bin").unlink()
    destination = tmp_path / "restored"
    with pytest.raises(CheckpointError, match="missing"):
        store.restore(reference, destination)
    assert not destination.exists()


def test_file_corruption_does_not_publish_partial_restore(snapshot, tmp_path):
    store, reference, _ = snapshot
    (store.root / str(reference.checkpoint_id) / "files" / "new.bin").write_bytes(b"broken")
    destination = tmp_path / "restored"
    with pytest.raises(CheckpointError, match="Snapshot content mismatch"):
        store.restore(reference, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".restoring-*"))


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape", "nested/../../escape", "a\\b"])
def test_unsafe_manifest_paths_are_rejected_before_writes(snapshot, tmp_path, path):
    store, reference, _ = snapshot
    manifest_path = store.root / str(reference.checkpoint_id) / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][0]["path"] = path
    data = json.dumps(manifest).encode()
    manifest_path.write_bytes(data)
    tampered = CheckpointRef(reference.checkpoint_id, hashlib.sha256(data).hexdigest())
    destination = tmp_path / "restored"
    with pytest.raises(CheckpointError):
        store.restore(tampered, destination)
    assert not destination.exists()


def test_uncommitted_staging_directory_is_not_loadable(tmp_path):
    store = LocalCheckpointStore(tmp_path / "store")
    pending = store.root / ".pending-test"
    pending.mkdir()
    (pending / "manifest.json").write_text("{}")
    with pytest.raises(CheckpointError, match="has not been committed"):
        store.load(CheckpointRef(uuid4(), "0" * 64))


def test_existing_destination_is_never_overwritten(snapshot, tmp_path):
    store, reference, _ = snapshot
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep").write_text("keep")
    with pytest.raises(FileExistsError):
        store.restore(reference, destination)
    assert (destination / "keep").read_text() == "keep"


def test_symlink_snapshot_is_rejected_and_staging_is_cleaned(tmp_path):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "link").symlink_to(tmp_path / "outside")
    store = LocalCheckpointStore(tmp_path / "store")
    with pytest.raises(CheckpointError, match="Unsupported snapshot entry"):
        store.commit(workspace, ExecutionRef(uuid4()), event_cursor=0, summary="")
    assert not list(store.root.iterdir())


def test_store_inside_workspace_is_rejected(tmp_path):
    store = LocalCheckpointStore(tmp_path / "store")
    with pytest.raises(CheckpointError, match="must be disjoint"):
        store.commit(tmp_path, ExecutionRef(uuid4()), event_cursor=0, summary="")


def test_mid_commit_write_failure_never_publishes_a_checkpoint(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"data")
    store = LocalCheckpointStore(tmp_path / "store")
    original = Path.write_bytes

    def fail_manifest(path, data):
        if path.name == "manifest.json":
            raise OSError("injected disk failure")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_manifest)
    with pytest.raises(OSError, match="injected disk failure"):
        store.commit(source, ExecutionRef(uuid4()), event_cursor=0, summary="")
    assert not list(store.root.iterdir())
