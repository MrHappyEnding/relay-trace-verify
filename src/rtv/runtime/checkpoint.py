"""Filesystem upload + conditional metadata commit; only the latter is restorable."""

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from rtv.checkpoints import CheckpointRef
from rtv.codex_agent import CODEX_VERSION, validate_bundle
from rtv.domain import ExecutionRef
from rtv.harbor_probe import HARBOR_VERSION, _docker, tree_digest
from rtv.runtime.config import execution_dir
from rtv.runtime.store import Store
from rtv.storage import LocalCheckpointStore


def artifacts(settings):
    return LocalCheckpointStore(settings.root / "checkpoints")


def reference(cp):
    return CheckpointRef(UUID(cp["checkpoint_id"]), cp["manifest_sha256"])


def validate(settings, cp, destination):
    storage = artifacts(settings)
    ref = reference(cp)
    manifest = storage.load(ref)
    if manifest.schema_version != 2 or manifest.context != cp["context"]:
        raise ValueError("Checkpoint recovery context differs from committed metadata")
    if (
        manifest.source.execution_id != UUID(cp["execution_id"])
        or manifest.source.epoch != cp["epoch"]
    ):
        raise ValueError("Checkpoint source identity mismatch")
    storage.restore(ref, destination)
    if tree_digest(destination / "workspace") != cp["context"]["workspace_digest"]:
        raise ValueError("Checkpoint workspace differs from verified boundary")
    return destination


def restore(settings, row, destination):
    cp = Store(settings).checkpoint(row["resume_from"])
    from rtv.runtime.handoff import load_context
    from rtv.runtime.relay_store import RelayStore

    handoff = RelayStore(settings).handoff(row["handoff_id"]) if row["handoff_id"] else None
    transferred = handoff and handoff["checkpoint_id"] == cp["checkpoint_id"]
    if cp["trial_id"] != row["trial_id"] and not transferred:
        raise ValueError("Recovery checkpoint belongs to a different trial")
    policy = load_context(settings, row)["policy"] if transferred else "native"
    context = cp["context"]
    for key in (
        ("task_digest", "image_id")
        if transferred
        else ("task_digest", "image_id", "adapter", "model")
    ):
        if context[key] != row["spec"][key]:
            raise ValueError(f"Recovery compatibility mismatch: {key}")
    if context["harbor_version"] != HARBOR_VERSION:
        raise ValueError("Incompatible Harbor version")
    _docker("image", "inspect", row["spec"]["image_id"])
    validate(settings, cp, destination)
    native = None
    if row["spec"]["adapter"] == "codex" and policy == "native":
        native = validate_bundle(destination, model=row["spec"]["model"])
    return cp, native


def boundary(settings, row, bundle, result, container_ids):
    if not container_ids:
        raise ValueError("Cannot checkpoint without a quiescence barrier")
    for container in container_ids:
        state = json.loads(_docker("inspect", "--format", "{{json .State}}", container))
        if state["Running"] or state["Restarting"] or state["Pid"] != 0:
            raise ValueError("Sandbox is not quiescent")
    store = Store(settings)
    events = [
        e
        for e in store.events(row["trial_id"])
        if e["payload"]["execution_id"] == row["execution_id"]
    ]
    return {
        "schema_version": 2,
        "stage": row["stage"],
        **{k: row["spec"][k] for k in ("task_digest", "image_id", "adapter", "model")},
        "harbor_version": HARBOR_VERSION,
        "cli_version": CODEX_VERSION if row["spec"]["adapter"] == "codex" else None,
        "parent": row["resume_from"],
        "workspace_scope": "/workspace",
        "workspace_digest": tree_digest(bundle / "workspace"),
        "event_boundary": [e["event_id"] for e in events],
        "event_cursor": max((e["sequence"] for e in events), default=0),
        "ledger_revision": store.get(row["trial_id"])["ledger_revision"],
        "quiescence": {"containers": container_ids, "stopped": True},
        "result": result,
    }


def publish(settings, row, bundle, context):
    store = Store(settings)
    cp = store.checkpoint_prepare(row, context)
    storage = artifacts(settings)
    directory = storage.root / cp["checkpoint_id"]
    if directory.exists():
        # Recover an upload acknowledgement lost after rename, before DB commit.
        digest = hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
        ref = CheckpointRef(UUID(cp["checkpoint_id"]), digest)
    else:
        ref = storage.commit(
            bundle,
            ExecutionRef.from_dict(row),
            event_cursor=context["event_cursor"],
            summary=f"completed stage {row['stage']}",
            context=context,
            checkpoint_id=UUID(cp["checkpoint_id"]),
        )
    uploaded = {**cp, "manifest_sha256": ref.manifest_sha256}
    with TemporaryDirectory(dir=execution_dir(settings, row), prefix="validate-") as temporary:
        validate(settings, uploaded, Path(temporary) / "bundle")
    return store.checkpoint_commit(row, cp["checkpoint_id"], ref.manifest_sha256, context)


def collect(settings, age_seconds=3600):
    store = Store(settings)
    root = artifacts(settings).root
    removed = []
    for cp in store.gc_candidates(age_seconds):
        # Metadata is tombstoned first; restore/commit/pin cannot race deletion.
        paths = [root / cp["checkpoint_id"], *root.glob(f".pending-{cp['checkpoint_id']}-*")]
        changed = cp["state"] != "deleted"
        for path in paths:
            if path.is_symlink():
                raise ValueError("Refusing GC through a symbolic link")
            if path.exists():
                shutil.rmtree(path)
                changed = True
        store.gc_done(cp["checkpoint_id"])
        if changed:
            removed.append(cp["checkpoint_id"])
    return {"deleted": removed, "retention_seconds": age_seconds}
