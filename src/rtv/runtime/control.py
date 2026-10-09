"""Short idempotent control operations, safe after Activity response loss."""

import json
import subprocess
import sys
import threading
from datetime import UTC, datetime

from rtv.runtime import sandbox
from rtv.runtime.config import TERMINAL, Settings
from rtv.runtime.files import atomic_json, lock
from rtv.runtime.store import Store
from rtv.runtime.supervisor import empty_result


def reconcile(settings: Settings, trial_id: str, *, launch: bool = True) -> dict:
    store = Store(settings)
    row = store.get(trial_id)
    if row["status"] in TERMINAL:
        return summary(row)
    output = settings.root / trial_id
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(output / "control.lock") as acquired:
        if not acquired:
            return summary(row)
        with lock(output / "runner.lock") as idle:
            if not idle:
                # A restarted worker observes the same IDs without relaunching.
                return summary(store.get(trial_id))
            row = store.get(trial_id)
            if row["status"] in TERMINAL:
                return summary(row)
            result_file = output / "result.json"
            if not result_file.exists():
                reason = None
                if row["cancel_requested"]:
                    reason = "cancelled"
                elif datetime.now(UTC) >= row["deadline"]:
                    reason = "ttl_expired"
                elif (output / "started.json").exists():
                    reason = "supervisor_lost"
                if reason:
                    atomic_json(result_file, empty_result(row, reason))
                elif launch:
                    # Candidate processes may race before acquiring runner.lock.
                    # Only the lock holder can publish the durable start intent;
                    # that intent forbids replay even if the holder later dies.
                    with (output / "supervisor.log").open("ab") as log:
                        process = subprocess.Popen(
                            [sys.executable, "-m", "rtv.runtime.supervisor", trial_id],
                            stdin=subprocess.DEVNULL,
                            stdout=log,
                            stderr=log,
                            start_new_session=True,
                            close_fds=True,
                        )
                    # Reap completed children while the worker remains alive.
                    threading.Thread(target=process.wait, daemon=True).start()
                    return summary(row)
                else:
                    return summary(row)
            result = json.loads(result_file.read_text())
            sandbox.cleanup(row)
            store.finish(trial_id, result)
            return summary(store.get(trial_id))


def summary(row: dict) -> dict:
    return {
        key: row[key]
        for key in (
            "trial_id",
            "operation_id",
            "attempt_id",
            "execution_id",
            "epoch",
            "status",
            "cancel_requested",
            "cleanup",
            "result",
        )
    }
