"""Manifest types and validation for the local, file-only checkpoint format."""

import re
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
class CheckpointManifest:
    checkpoint_id: UUID
    source: ExecutionRef
    event_cursor: int
    summary: str
    directories: tuple[str, ...]
    files: tuple[FileEntry, ...]
    schema_version: int = 1
    context: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise CheckpointError("Unsupported manifest schema version")
        if self.schema_version == 2 and not isinstance(self.context, dict):
            raise CheckpointError("Version 2 requires recovery context")
        if self.schema_version == 1 and self.context is not None:
            raise CheckpointError("Version 1 does not support recovery context")
        if type(self.event_cursor) is not int or self.event_cursor < 0:
            raise CheckpointError("Invalid event cursor")
        if not isinstance(self.summary, str):
            raise CheckpointError("Summary must be a string")
        directories = [validate_relative_path(path) for path in self.directories]
        paths = directories + [entry.path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise CheckpointError("Duplicate snapshot paths")
        directory_set = set(directories)
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
        if self.schema_version == 2:
            result["context"] = self.context
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
            if isinstance(value, dict) and value.get("schema_version") == 2:
                required.add("context")
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
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CheckpointError(f"Invalid checkpoint manifest: {exc}") from exc
