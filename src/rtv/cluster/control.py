"""Retryable cluster control with UID cleanup and no implicit model replay."""

import json
from datetime import UTC, datetime

from rtv.cluster.config import ClusterConfig, pool_label
from rtv.cluster.kubernetes import Kubernetes, job_manifest, validate_job
from rtv.cluster.store import ClusterStore
from rtv.runtime.config import TERMINAL, execution_dir
from rtv.runtime.supervisor import empty_result


def cleanup(store, backend, row):
    allocation = store.allocation(row["execution_id"])
    if allocation is None:
        return True
    if not backend.remove(allocation, pool_label(store.settings)):
        return False
    store.release(row)
    return True


def reconcile(settings, trial_id, *, launch=True):
    from rtv.runtime.control import recover, summary
    from rtv.runtime.handoff import prepare
    from rtv.runtime.relay_store import RelayStore

    config = ClusterConfig.load()
    config.validate_root(settings)
    store = ClusterStore(settings)
    store.verify_pool(config)
    with store.reconcile_lock(trial_id) as held:
        row = store.get(trial_id)
        if not held:
            return summary(row)
        backend = Kubernetes(config)
        try:
            for old in store.executions(trial_id):
                if old["execution_id"] != row["execution_id"]:
                    cleanup(store, backend, old)
            if row["status"] in TERMINAL | {"sealed", "checkpointed", "preparing"}:
                cleaned = cleanup(store, backend, row)
                if row["status"] in TERMINAL:
                    return summary(store.get(trial_id))
            else:
                cleaned = False
            expired = datetime.now(UTC) >= row["deadline"]
            if row["cancel_requested"] or expired:
                relays = RelayStore(settings)
                handoff = relays.pending_handoff(trial_id)
                if handoff:
                    relays.abort(
                        handoff["handoff_id"],
                        "cancelled" if row["cancel_requested"] else "ttl_expired",
                    )
                if cleanup(store, backend, row):
                    store.finish(
                        row,
                        empty_result(
                            row, "cancelled" if row["cancel_requested"] else "ttl_expired"
                        ),
                        controller=True,
                    )
                return summary(store.get(trial_id))
            if row["status"] == "checkpointed":
                if cleaned:
                    return summary(
                        recover(settings, row, "stage:" + row["execution_id"], recovery=False)
                    )
                return summary(row)
            if row["status"] == "sealed":
                return summary(row)
            if row["status"] == "preparing":
                relays = RelayStore(settings)
                handoff = relays.pending_handoff(trial_id)
                if handoff:
                    try:
                        prepare(settings, handoff["handoff_id"])
                    except Exception as exc:
                        if relays.handoff(handoff["handoff_id"])["state"] != "completed":
                            relays.abort(handoff["handoff_id"], type(exc).__name__)
                            if handoff["kind"] == "branch":
                                store.finish(
                                    row,
                                    empty_result(row, "handoff_prepare_failed"),
                                    controller=True,
                                )
                return summary(store.get(trial_id))

            allocation = store.allocation(row["execution_id"])
            if allocation is None:
                if not launch:
                    return summary(row)
                from rtv.cluster.credentials import admitted_reference
                from rtv.cluster.repository import validate_spec

                try:
                    validate_spec(config.frozen(), row["spec"])
                    admitted_reference(config.frozen(), settings.root, row)
                except ValueError:
                    store.finish(row, empty_result(row, "invalid_cluster_task"), controller=True)
                    return summary(store.get(trial_id))
                allocation = store.reserve(row)
                if allocation is None:
                    return summary(row)
            job = backend.job(allocation["job_name"])
            if (
                job is None
                and allocation["job_uid"] is None
                and allocation["state"] != "released"
                and row["execution_state"] == "queued"
            ):
                if launch:
                    job = backend.ensure(job_manifest(settings, config, row, allocation))
                else:
                    return summary(row)
            pods = backend.pods(allocation)
            if job:
                validate_job(job, job_manifest(settings, config, row, allocation))
                store.observed(row, job, pods)
            row = store.get(trial_id)
            if row["lease_valid"] and row["execution_state"] == "running":
                return summary(row)
            # Commit/result acknowledgement loss must not replay a completed turn.
            result = None
            if row["latest_checkpoint"]:
                cp = store.checkpoint(row["latest_checkpoint"])
                if cp["execution_id"] == row["execution_id"]:
                    result = {**cp["context"]["result"], "checkpoint_id": cp["checkpoint_id"]}
            path = execution_dir(settings, row) / "result.json"
            if result is None and path.is_file():
                result = json.loads(path.read_text())
            failed = job is None or any(
                c.get("type") in {"Failed", "Complete"} and c.get("status") == "True"
                for c in (job.get("status", {}).get("conditions", []) if job else [])
            )
            lost = row["execution_state"] == "running" and not row["lease_valid"]
            if result is None and (lost or failed):
                if (row["latest_checkpoint"] or row["resume_from"]) and row["recoveries"] < row[
                    "spec"
                ].get("max_recoveries", 0):
                    # Each epoch has a different Job and writable directory. Keep
                    # the old allocation charged until deletion is acknowledged.
                    cleanup(store, backend, row)
                    return summary(recover(settings, row, "loss:" + row["execution_id"]))
                result = empty_result(row, "executor_lost" if lost else "job_failed")
            if result is not None and cleanup(store, backend, row):
                store.finish(row, result, controller=True)
            return summary(store.get(trial_id))
        finally:
            backend.close()
