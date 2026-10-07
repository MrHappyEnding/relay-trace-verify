"""Single-process local storage. This is not a sandbox or distributed ownership service."""

import hashlib
import json
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from rtv.checkpoints import CheckpointError, CheckpointManifest, CheckpointRef, FileEntry
from rtv.domain import ExecutionRef


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


class LocalCheckpointStore:
    """Publish a stage snapshot only after all files and the manifest have been written.

    The caller must establish quiescence. Only regular files and directories are supported.
    The reference digest protects manifest integrity; it is not an authenticity signature.
    Concurrent writers and host power-loss durability are outside this local prototype.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def commit(
        self, workspace: Path, execution: ExecutionRef, *, event_cursor: int, summary: str
    ) -> CheckpointRef:
        if workspace.is_symlink() or not workspace.is_dir():
            raise CheckpointError("Workspace must be a real directory")
        workspace = workspace.resolve()
        if _is_within(self.root, workspace) or _is_within(workspace, self.root):
            raise CheckpointError("Workspace and checkpoint store must be disjoint")
        checkpoint_id = uuid4()
        destination = self.root / str(checkpoint_id)
        with TemporaryDirectory(prefix=".pending-", dir=self.root) as temporary:
            staging = Path(temporary)
            snapshot = staging / "files"
            snapshot.mkdir()
            entries = []
            directories = []
            for source in sorted(workspace.rglob("*")):
                relative = source.relative_to(workspace).as_posix()
                metadata = source.lstat()
                target = snapshot / relative
                if stat.S_ISDIR(metadata.st_mode):
                    directories.append(relative)
                    target.mkdir()
                elif stat.S_ISREG(metadata.st_mode):
                    data = source.read_bytes()
                    mode = stat.S_IMODE(metadata.st_mode) & 0o777
                    target.write_bytes(data)
                    target.chmod(mode)
                    entries.append(FileEntry(relative, _sha256(data), len(data), mode))
                else:
                    raise CheckpointError(f"Unsupported snapshot entry: {relative}")
            manifest = CheckpointManifest(
                checkpoint_id=checkpoint_id,
                source=execution,
                event_cursor=event_cursor,
                summary=summary,
                directories=tuple(directories),
                files=tuple(entries),
            )
            data = json.dumps(manifest.to_dict(), sort_keys=True, indent=2).encode("utf-8")
            (staging / "manifest.json").write_bytes(data)
            os.rename(staging, destination)
        return CheckpointRef(checkpoint_id, _sha256(data))

    def load(self, reference: CheckpointRef) -> CheckpointManifest:
        try:
            data = (self.root / str(reference.checkpoint_id) / "manifest.json").read_bytes()
        except FileNotFoundError as exc:
            raise CheckpointError("Checkpoint has not been committed or does not exist") from exc
        if _sha256(data) != reference.manifest_sha256:
            raise CheckpointError("Manifest digest mismatch")
        try:
            manifest = CheckpointManifest.from_dict(json.loads(data))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CheckpointError("Invalid manifest JSON") from exc
        if manifest.checkpoint_id != reference.checkpoint_id:
            raise CheckpointError("Checkpoint identity mismatch")
        return manifest

    def restore(self, reference: CheckpointRef, destination: Path) -> Path:
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"Restore destination already exists: {destination}")
        destination = destination.resolve()
        if _is_within(destination, self.root) or _is_within(self.root, destination):
            raise CheckpointError("Restore destination and checkpoint store must be disjoint")
        manifest = self.load(reference)
        snapshot = self.root / str(reference.checkpoint_id) / "files"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".restoring-", dir=destination.parent) as temporary:
            staging = Path(temporary)
            for directory in manifest.directories:
                (staging / directory).mkdir(parents=True, exist_ok=True)
            for entry in manifest.files:
                source = snapshot / entry.path
                # Reject links in every component, including directories.
                if any(path.is_symlink() for path in (source, *source.parents)):
                    raise CheckpointError(f"Snapshot contains a symbolic link: {entry.path}")
                if not source.is_file():
                    raise CheckpointError(f"Snapshot file missing or not regular: {entry.path}")
                try:
                    data = source.read_bytes()
                except FileNotFoundError as exc:
                    raise CheckpointError(f"Snapshot file missing: {entry.path}") from exc
                if len(data) != entry.size or _sha256(data) != entry.sha256:
                    raise CheckpointError(f"Snapshot content mismatch: {entry.path}")
                target = staging / entry.path
                target.write_bytes(data)
                target.chmod(entry.mode)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"Restore destination already exists: {destination}")
            os.rename(staging, destination)
        return destination


class LocalWorkspaceBackend:
    """Independent local directories for trusted fixture agents; no process isolation."""

    def __init__(self, root: Path, checkpoints: LocalCheckpointStore):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkpoints = checkpoints

    def prepare(self, execution: ExecutionRef, checkpoint: CheckpointRef | None = None) -> Path:
        workspace = self.root / str(execution.execution_id)
        if checkpoint is None:
            workspace.mkdir()
        else:
            self.checkpoints.restore(checkpoint, workspace)
        return workspace
