from uuid import uuid4

import pytest

from rtv.checkpoints import CheckpointError, CheckpointManifest, SymlinkEntry, validate_symlinks
from rtv.domain import ExecutionRef
from rtv.harbor_probe import tree_digest
from rtv.storage import LocalCheckpointStore


def commit(store, source):
    return store.commit(
        source,
        ExecutionRef(uuid4()),
        event_cursor=0,
        summary="links",
        context={},
        preserve_directory_modes=True,
        preserve_symlinks=True,
    )


def test_v4_preserves_link_targets_without_dereferencing_and_isolates_branches(tmp_path):
    source = tmp_path / "source"
    (source / "real").mkdir(parents=True)
    (source / "real/file").write_text("original")
    (source / "file-link").symlink_to("real/file")
    (source / "directory-link").symlink_to("real", target_is_directory=True)
    (source / "chain").symlink_to("file-link")
    (source / "dangling").symlink_to("not-created-yet")
    store = LocalCheckpointStore(tmp_path / "store")
    ref = commit(store, source)
    manifest = store.load(ref)
    assert manifest.schema_version == 4 and len(manifest.symlinks) == 4
    assert len(manifest.files) == 1
    assert CheckpointManifest.from_dict(manifest.to_dict()) == manifest
    restored = store.restore(ref, tmp_path / "restored")
    assert tree_digest(restored, preserve_symlinks=True) == tree_digest(
        source, preserve_symlinks=True
    )
    assert (restored / "dangling").is_symlink() and not (restored / "dangling").exists()
    (restored / "chain").write_text("branch change")
    other = store.restore(ref, tmp_path / "other")
    assert (other / "chain").read_text() == (source / "chain").read_text() == "original"
    (source / "file-link").unlink()
    (source / "file-link").symlink_to("./real/file")
    assert tree_digest(source, preserve_symlinks=True) != tree_digest(other, preserve_symlinks=True)


@pytest.mark.parametrize(
    "links",
    [
        {"link": "../outside"},
        {"link": "/etc/passwd"},
        {"a": "b", "b": "a"},
        {"inside/up": "..", "escape": "inside/up/../outside"},
    ],
)
def test_links_cannot_escape_through_parent_chains_or_cycles(tmp_path, links):
    source = tmp_path / "source"
    source.mkdir()
    for name, target in links.items():
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(target)
    store = LocalCheckpointStore(tmp_path / "store")
    with pytest.raises(CheckpointError):
        tree_digest(source, preserve_symlinks=True)
    with pytest.raises(CheckpointError):
        commit(store, source)
    assert not list(store.root.iterdir())


def test_v4_detects_link_tampering_before_restore_publication(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("content")
    (source / "link").symlink_to("file")
    store = LocalCheckpointStore(tmp_path / "store")
    ref = commit(store, source)
    link = store.root / str(ref.checkpoint_id) / "files/link"
    link.unlink()
    link.symlink_to("../outside")
    with pytest.raises(CheckpointError, match="target mismatch"):
        store.restore(ref, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_parent_relative_link_inside_root_and_old_v3_digest_remain_supported(tmp_path):
    validate_symlinks((SymlinkEntry("docs/assets/link", "../../data/file"),))
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_text("data")
    before = tree_digest(source)
    store = LocalCheckpointStore(tmp_path / "store")
    ref = commit(store, source)
    assert store.load(ref).schema_version == 3
    assert tree_digest(store.restore(ref, tmp_path / "restored"), preserve_symlinks=True) == before
