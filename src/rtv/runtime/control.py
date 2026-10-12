"""Retryable control operations, epoch takeover and orphan reconciliation."""

import json
import subprocess
import sys
import threading
from datetime import UTC, datetime

from rtv.runtime import checkpoint, sandbox
from rtv.runtime.config import TERMINAL, Settings, execution_dir
from rtv.runtime.files import lock
from rtv.runtime.store import Store
from rtv.runtime.supervisor import empty_result


def recover(settings, row, key, *, recovery=True):
    store = Store(settings)
    if store.recovery_result(row["trial_id"], key):
        return store.get(row["trial_id"])
    # Verify the exact committed input before transferring authority. The new
    # execution verifies again at restore; corruption can never trigger a turn.
    if not row["latest_checkpoint"]:
        raise ValueError("No committed checkpoint")
    from pathlib import Path
    from tempfile import TemporaryDirectory

    cp = store.checkpoint(row["latest_checkpoint"])
    if cp["stage"] >= row["spec"].get("stages", 1):
        raise ValueError("All planned stages already completed")
    with TemporaryDirectory(dir=settings.root, prefix="recovery-check-") as temporary:
        checkpoint.validate(settings, cp, Path(temporary) / "bundle")
    store.advance(row, key, recovery=recovery)
    return store.get(row["trial_id"])


def reconcile(settings: Settings, trial_id: str, *, launch=True):
    if settings.schema == "rtv4":
        from rtv.cluster.control import reconcile as remote_reconcile

        return remote_reconcile(settings, trial_id, launch=launch)
    store = Store(settings)
    row = store.get(trial_id)
    trial_dir = settings.root / trial_id
    trial_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(trial_dir / "control.lock") as acquired:
        if not acquired:
            return summary(row)
        row = store.get(trial_id)
        # Retired identities stay queryable. Sweep them even after an earlier
        # cleanup acknowledgement, including delayed creation by an old client.
        for old in store.executions(trial_id):
            if old["execution_id"] != row["execution_id"]:
                sandbox.cleanup(old)
                store.cleaned(old["execution_id"])
        if row["status"] in TERMINAL:
            return summary(row)
        if row["status"] in {"sealed", "preparing"}:
            expired = datetime.now(UTC) >= row["deadline"]
            if not row["cancel_requested"] and not expired:
                if row["status"] == "sealed":
                    return summary(row)
                from rtv.runtime.handoff import prepare
                from rtv.runtime.relay_store import RelayStore

                relays = RelayStore(settings)
                h = relays.pending_handoff(trial_id)
                if h:
                    try:
                        prepare(settings, h["handoff_id"])
                        return summary(store.get(trial_id))
                    except Exception as exc:
                        if relays.handoff(h["handoff_id"])["state"] == "completed":
                            return summary(store.get(trial_id))
                        relays.abort(h["handoff_id"], type(exc).__name__)
                        if h["kind"] == "relay":
                            return summary(store.get(trial_id))
                        sandbox.cleanup(row)
                        store.finish(
                            row, empty_result(row, "handoff_prepare_failed"), controller=True
                        )
                        return summary(store.get(trial_id))
        if row["cancel_requested"]:
            from rtv.runtime.relay_store import RelayStore

            h = RelayStore(settings).pending_handoff(trial_id)
            if h:
                RelayStore(settings).abort(h["handoff_id"], "cancelled")
        if row["status"] == "checkpointed":
            return summary(recover(settings, row, "stage:" + row["execution_id"], recovery=False))
        output = execution_dir(settings, row)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock(output / "runner.lock") as idle:
            row = store.get(trial_id)
            # Loss of worker alone leaves the supervisor's DB lease valid.
            if not idle and row["lease_valid"]:
                return summary(row)
            if not idle and row["execution_state"] == "queued":
                return summary(row)
            result = None
            # A committed boundary wins over a lost upload/commit/result reply.
            if row["latest_checkpoint"]:
                cp = store.checkpoint(row["latest_checkpoint"])
                if cp["execution_id"] == row["execution_id"]:
                    result = {**cp["context"]["result"], "checkpoint_id": cp["checkpoint_id"]}
            if result is None and (output / "result.json").exists():
                result = json.loads((output / "result.json").read_text())
            reason = None
            if row["cancel_requested"]:
                reason = "cancelled"
            elif datetime.now(UTC) >= row["deadline"]:
                reason = "ttl_expired"
            elif result is None and row["execution_state"] == "running":
                reason = "supervisor_lost" if idle else "lease_expired"
            elif result is None and row["execution_state"] == "queued":
                if launch:
                    with (output / "supervisor.log").open("ab") as log:
                        process = subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "rtv.runtime.supervisor",
                                trial_id,
                                row["execution_id"],
                            ],
                            stdin=subprocess.DEVNULL,
                            stdout=log,
                            stderr=log,
                            start_new_session=True,
                            close_fds=True,
                        )
                    threading.Thread(target=process.wait, daemon=True).start()
                return summary(row)
            if (
                reason in {"supervisor_lost", "lease_expired"}
                and row["latest_checkpoint"]
                and row["recoveries"] < row["spec"].get("max_recoveries", 0)
            ):
                return summary(recover(settings, row, "loss:" + row["execution_id"]))
            result = empty_result(row, reason) if reason else result
            if result is None:
                return summary(row)
            sandbox.cleanup(row)
            store.finish(row, result, controller=True)
            return summary(store.get(trial_id))


def summary(row):
    return {
        k: row[k]
        for k in (
            "trial_id",
            "operation_id",
            "attempt_id",
            "execution_id",
            "epoch",
            "stage",
            "status",
            "cancel_requested",
            "cleanup",
            "result",
            "latest_checkpoint",
            "recoveries",
        )
    }
