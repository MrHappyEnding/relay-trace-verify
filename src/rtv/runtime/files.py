"""Single-host process locks and atomic local result publication (not M2 fencing)."""

import fcntl
import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_json(path: Path, data: dict):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def admission(root: Path, capacity: int):
    """Kernel releases a local execution slot if its supervisor dies."""
    if not 1 <= capacity <= 8:
        raise ValueError("RTV_MAX_EXECUTIONS must be between 1 and 8")
    with ExitStack() as stack:
        for number in range(capacity):
            if stack.enter_context(lock(root / f"slot-{number}.lock")):
                yield True
                return
        yield False
