"""Lossless archival of completed local runtime materializations, with readback verification."""

import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from uuid import UUID


def archive_completed(root):
    root = Path(root)
    report = json.loads((root / "report.json").read_text())
    if not report.get("checks", {}).get("all_allocations_released"):
        raise ValueError("Cannot archive a runtime with unreleased allocations")
    targets = []
    for path in root.iterdir():
        if not path.is_dir() or path.name == "observations":
            continue
        if path.name not in {"checkpoints", "handoffs"}:
            UUID(path.name)  # Only this runtime's known artifact directories.
        targets.append(path)
    archive = root / "artifacts.tar.gz"
    receipt = root / "archive.json"
    if not targets:
        return json.loads(receipt.read_text()) if receipt.exists() else None
    if not archive.exists():
        temporary = root / "artifacts.partial.tar.gz"
        with tarfile.open(temporary, "w:gz", compresslevel=3, dereference=False) as tar:
            for path in sorted(targets):
                tar.add(path, arcname=path.name)
        temporary.replace(archive)
    # Read every compressed byte back and compare every still-materialized file/link.
    seen, files, size = set(), 0, 0
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe archive member")
            path = root / relative
            seen.add(member.name)
            if member.isfile():
                stored = hashlib.file_digest(tar.extractfile(member), "sha256").hexdigest()
                if path.exists():
                    with path.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    if actual != stored or path.stat().st_mode & 0o7777 != member.mode:
                        raise ValueError("Archive readback differs from original")
                files += 1
                size += member.size
            elif member.issym() and path.is_symlink():
                if str(path.readlink()) != member.linkname:
                    raise ValueError("Archive symlink changed")
    for directory in targets:
        for path in [directory, *directory.rglob("*")]:
            if path.relative_to(root).as_posix() not in seen:
                raise ValueError("Artifact missing from archive")
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {
        "archive": archive.name,
        "sha256": checksum,
        "files": files,
        "uncompressed_bytes": size,
        "compressed_bytes": archive.stat().st_size,
        "restore": "tar -xzf artifacts.tar.gz (inside the original runtime root)",
        "readback_verified": True,
    }
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    for path in targets:
        shutil.rmtree(path)
    return result
