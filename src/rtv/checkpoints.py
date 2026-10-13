"""Manifest types and validation for the local, file-only checkpoint format."""

import re
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from rtv.domain import ExecutionRef


class CheckpointError(ValueError):
    pass


def validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise CheckpointError("Invalid snapshot path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or value == ".":
        raise CheckpointError(f"Snapshot path must be a canonical relative path: {value!r}")
    return value


def validate_digest(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise CheckpointError("Expected a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class CheckpointRef:
    checkpoint_id: UUID
    manifest_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, UUID):
            raise CheckpointError("Checkpoint identity must be a UUID object")
        validate_digest(self.manifest_sha256)

    def to_dict(self) -> dict[str, str]:
        return {
            "checkpoint_id": str(self.checkpoint_id),
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str
    size: int
    mode: int

    def __post_init__(self) -> None:
        validate_relative_path(self.path)
        validate_digest(self.sha256)
        if type(self.size) is not int or self.size < 0:
            raise CheckpointError("File size must be a nonnegative integer")
        if type(self.mode) is not int or not 0 <= self.mode <= 0o777:
            raise CheckpointError("Unsupported file mode")


@dataclass(frozen=True)
class SymlinkEntry:
    path: str
    target: str

    def __post_init__(self) -> None:
        validate_relative_path(self.path)
        if (
            not isinstance(self.target, str)
            or not self.target
            or "\\" in self.target
            or "\x00" in self.target
            or PurePosixPath(self.target).is_absolute()
        ):
            raise CheckpointError("Symbolic link target must be a relative POSIX path")


def validate_symlinks(entries: tuple[SymlinkEntry, ...]) -> None:
    """Resolve link chains lexically, without reading a referent outside the snapshot."""
    links = {entry.path: entry.target for entry in entries}
    if len(links) != len(entries):
        raise CheckpointError("Duplicate symbolic link paths")
    for name in links:
        pending = deque(PurePosixPath(name).parts)
        resolved = []
        expansions = 0
        while pending:
            part = pending.popleft()
            if part == ".":
                continue
            if part == "..":
                if not resolved:
                    raise CheckpointError("Symbolic link escapes the snapshot root")
                resolved.pop()
                continue
            candidate = "/".join([*resolved, part])
            if candidate in links:
                expansions += 1
                if expansions > 128:
                    raise CheckpointError("Cyclic or excessively deep symbolic link chain")
                pending.extendleft(reversed(PurePosixPath(links[candidate]).parts))
            else:
                resolved.append(part)


@dataclass(frozen=True)
class CheckpointManifest:
    checkpoint_id: UUID
    source: ExecutionRef
    event_cursor: int
    summary: str
    directories: tuple[str, ...]
    files: tuple[FileEntry, ...]
    schema_version: int = 1
    context: dict[str, Any] | None = None
    directory_modes: dict[str, int] | None = None
    excluded_names: tuple[str, ...] = ()
    symlinks: tuple[SymlinkEntry, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in (1, 2, 3, 4):
            raise CheckpointError("Unsupported manifest schema version")
        if self.schema_version in (2, 3, 4) and not isinstance(self.context, dict):
            raise CheckpointError("Version 2/3/4 requires recovery context")
        if self.schema_version == 1 and self.context is not None:
            raise CheckpointError("Version 1 does not support recovery context")
        if type(self.event_cursor) is not int or self.event_cursor < 0:
            raise CheckpointError("Invalid event cursor")
        if not isinstance(self.summary, str):
            raise CheckpointError("Summary must be a string")
        directories = [validate_relative_path(path) for path in self.directories]
        paths = directories + [entry.path for entry in self.files] + [e.path for e in self.symlinks]
        if self.symlinks and self.schema_version != 4:
            raise CheckpointError("Symbolic links require version 4")
        validate_symlinks(self.symlinks)
        if len(paths) != len(set(paths)):
            raise CheckpointError("Duplicate snapshot paths")
        directory_set = set(directories)
        if self.excluded_names not in ((), (".DS_Store",)) or (
            self.excluded_names and self.schema_version not in (3, 4)
        ):
            raise CheckpointError("Unsupported snapshot exclusions")
        if any(part in self.excluded_names for path in paths for part in PurePosixPath(path).parts):
            raise CheckpointError("Manifest includes an excluded entry")
        if self.schema_version in (3, 4):
            if (
                not isinstance(self.directory_modes, dict)
                or set(self.directory_modes) != directory_set
            ):
                raise CheckpointError("Version 3/4 requires a mode for every directory")
            if any(
                type(m) is not int or not 0 <= m <= 0o777 for m in self.directory_modes.values()
            ):
                raise CheckpointError("Unsupported directory mode")
        elif self.directory_modes is not None:
            raise CheckpointError("Directory modes require version 3/4")
        for path in paths:
            for parent in PurePosixPath(path).parents:
                if str(parent) != "." and str(parent) not in directory_set:
                    raise CheckpointError(f"Missing or conflicting parent directory for {path}")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "checkpoint_id": str(self.checkpoint_id),
            "source": self.source.to_dict(),
            "event_cursor": self.event_cursor,
            "summary": self.summary,
            "directories": list(self.directories),
            "files": [asdict(entry) for entry in self.files],
        }
        if self.schema_version in (2, 3, 4):
            result["context"] = self.context
        if self.schema_version in (3, 4):
            result["directory_modes"] = self.directory_modes
            result["excluded_names"] = list(self.excluded_names)
        if self.schema_version == 4:
            result["symlinks"] = [asdict(entry) for entry in self.symlinks]
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CheckpointManifest":
        try:
            required = {
                "schema_version",
                "checkpoint_id",
                "source",
                "event_cursor",
                "summary",
                "directories",
                "files",
            }
            if isinstance(value, dict) and value.get("schema_version") in (2, 3, 4):
                required.add("context")
            if isinstance(value, dict) and value.get("schema_version") in (3, 4):
                required.add("directory_modes")
                required.add("excluded_names")
            if isinstance(value, dict) and value.get("schema_version") == 4:
                required.add("symlinks")
                if not isinstance(value.get("symlinks"), list):
                    raise CheckpointError("Manifest symbolic links must be an array")
            if not isinstance(value, dict) or set(value) != required:
                raise CheckpointError("Manifest fields do not match its schema version")
            if not isinstance(value["directories"], list) or not isinstance(value["files"], list):
                raise CheckpointError("Manifest directories and files must be arrays")
            return cls(
                checkpoint_id=UUID(value["checkpoint_id"]),
                source=ExecutionRef.from_dict(value["source"]),
                event_cursor=value["event_cursor"],
                summary=value["summary"],
                directories=tuple(value["directories"]),
                files=tuple(FileEntry(**entry) for entry in value["files"]),
                schema_version=value["schema_version"],
                context=value.get("context"),
                directory_modes=value.get("directory_modes"),
                excluded_names=tuple(value.get("excluded_names", [])),
                symlinks=tuple(SymlinkEntry(**e) for e in value.get("symlinks", [])),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CheckpointError(f"Invalid checkpoint manifest: {exc}") from exc
