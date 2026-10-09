"""Real PostgreSQL + Temporal + Docker faults. No model calls. New output per run.

Run after local services and `rtv runtime prepare` are ready.
The fault worker deliberately loses an Activity response after its side effect.
"""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from rtv.runtime import sandbox
from rtv.runtime.cli import make_spec
from rtv.runtime.config import Settings, execution_dir
from rtv.runtime.control import reconcile, summary
from rtv.runtime.service import Activities, ensure_workflow
from rtv.runtime.store import Store
from rtv.runtime.workflow import TrialWorkflow


async def fault_worker():
    settings = Settings.load()
    original = Activities(settings)

    @activity.defn(name="rtv_reconcile")
    def lose_response(trial_id: str) -> dict:
        result = original.poll(trial_id)
        (settings.root / "response-pending").write_text(trial_id)
        time.sleep(120)  # Parent SIGKILLs this owned test worker before acknowledgement.
        return result

    client = await Client.connect(settings.temporal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        async with Worker(
            client,
            task_queue=settings.queue,
            workflows=[TrialWorkflow],
            activities=[lose_response, original.cancel],
            activity_executor=pool,
        ):
            await asyncio.Event().wait()


def wait_for(predicate, timeout=90):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(0.4)
    raise TimeoutError("Acceptance condition did not converge")


def main(output: Path):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["RTV_RUNTIME_ROOT"] = str(output)
    settings = Settings.load()
    store = Store(settings)
    store.initialize()
    checks = {}
    rows = []
    workers = []
    began = time.monotonic()

    def check(name, value):
        checks[name] = bool(value)
        if not value:
            raise AssertionError(name)

    def start_worker(fault=False):
        command = (
            [sys.executable, __file__, "--fault-worker"]
            if fault
            else [sys.executable, "-m", "rtv", "runtime", "worker"]
        )
        with (output / f"worker-{len(workers)}.log").open("wb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=log)
        workers.append(process)
        return process

    def submit(key, delay=0, ttl=180):
        spec = make_spec(
            Namespace(
                adapter="fixture",
                ttl=ttl,
                delay=delay,
                image=None,
                model=None,
                allow_model_calls=False,
            )
        )
        row = store.submit(key, spec)
        rows.append(row)
        return row

    async def dispatch(row):
        client = await Client.connect(settings.temporal)
        await ensure_workflow(client, settings, row["trial_id"])

    def event(row, kind):
        return next((e for e in store.events(row["trial_id"]) if e["kind"] == kind), None)

    def finished(row):
        current = store.get(row["trial_id"])
        return current if current["cleanup"] == "complete" else None

    try:
        worker = start_worker(fault=True)
        row = submit("response-loss", delay=18)
        asyncio.run(dispatch(row))
        ready = wait_for(lambda: event(row, "sandbox.ready"))
        wait_for(lambda: event(row, "agent.started"))
        wait_for(lambda: (output / "response-pending").exists())
        before = sandbox.resources(row)
        worker.kill()
        worker.wait(timeout=10)
        check("worker_sigkill_did_not_stop_sandbox", before == sandbox.resources(row))
        start_worker()
        repeated = store.submit("response-loss", row["spec"])
        asyncio.run(dispatch(repeated))
        check(
            "duplicate_submission_same_identity",
            summary(row) == summary(repeated)
            or all(
                row[k] == repeated[k]
                for k in ("trial_id", "attempt_id", "execution_id", "operation_id")
            ),
        )
        try:
            store.submit("response-loss", {**row["spec"], "delay_seconds": 20})
        except ValueError:
            check("conflicting_duplicate_rejected", True)
        else:
            check("conflicting_duplicate_rejected", False)
        completed = wait_for(lambda: finished(row))
        check("worker_restart_trial_verified", completed["status"] == "succeeded")
        check(
            "same_supervisor_after_restart",
            ready["payload"]["supervisor_pid"]
            == json.loads((execution_dir(settings, row) / "started.json").read_text())["pid"],
        )
        check(
            "single_effective_agent_start",
            len([e for e in store.events(row["trial_id"]) if e["kind"] == "agent.started"]) == 1,
        )

        async def workflow_evidence():
            client = await Client.connect(settings.temporal)
            handle = client.get_workflow_handle("rtv2-" + row["trial_id"])
            result = await asyncio.wait_for(handle.result(), 90)
            history = await handle.fetch_history()
            (output / "temporal-history.json").write_text(history.to_json())
            await Replayer(workflows=[TrialWorkflow]).replay_workflow(history)
            check("workflow_history_replays", True)
            check("workflow_converged_after_response_loss", result["status"] == "succeeded")
            attempts = [
                e.activity_task_started_event_attributes.attempt
                for e in history.events
                if e.HasField("activity_task_started_event_attributes")
            ]
            check("temporal_activity_retried", any(a > 1 for a in attempts))
            query = await handle.query(TrialWorkflow.status)
            check("workflow_query_matches_product", query["status"] == completed["status"])

        asyncio.run(workflow_evidence())
        again = store.submit("response-loss", row["spec"])
        asyncio.run(dispatch(again))
        check("closed_trial_not_reexecuted", again["status"] == "succeeded")

        queued = submit("cancel-before-start")
        store.cancel(queued["trial_id"])
        reconcile(settings, queued["trial_id"], launch=False)
        check(
            "queued_cancel_no_execution",
            finished(queued)["status"] == "cancelled"
            and not (execution_dir(settings, queued) / "started.json").exists(),
        )

        # No explicit Temporal dispatch: the production worker repairs the DB/start gap.
        outbox = submit("dispatch-gap", delay=0)
        check(
            "db_to_temporal_gap_repaired",
            wait_for(lambda: finished(outbox))["status"] == "succeeded",
        )

        cancelled = submit("cancel-running", delay=90)
        wait_for(lambda: event(cancelled, "agent.started"))
        store.cancel(cancelled["trial_id"])
        store.cancel(cancelled["trial_id"])
        check(
            "running_cancel_converged",
            wait_for(lambda: finished(cancelled))["status"] == "cancelled",
        )

        ttl = submit("ttl-running", delay=90, ttl=15)
        wait_for(lambda: event(ttl, "agent.started"))
        worker = workers[-1]
        worker.kill()
        worker.wait(timeout=10)
        expired = wait_for(lambda: finished(ttl))
        check("ttl_enforced_while_worker_down", expired["result"]["reason"] == "ttl_expired")
        start_worker()

        lost = submit("supervisor-lost", delay=90)
        wait_for(lambda: event(lost, "agent.started"))
        pid = json.loads((execution_dir(settings, lost) / "started.json").read_text())["pid"]
        os.kill(pid, signal.SIGKILL)
        final = wait_for(lambda: finished(lost))
        check("lost_supervisor_not_replayed", final["result"]["reason"] == "supervisor_lost")

        # A cleanup response may fail; result remains pending and retries converge.
        workers[-1].terminate()
        workers[-1].wait(timeout=10)
        pending = submit("cleanup-retry")
        store.cancel(pending["trial_id"])
        original = sandbox.cleanup

        def unavailable(row):
            raise RuntimeError("injected Docker cleanup outage")

        sandbox.cleanup = unavailable
        try:
            try:
                reconcile(settings, pending["trial_id"], launch=False)
            except RuntimeError:
                check(
                    "cleanup_failure_not_marked_complete",
                    store.get(pending["trial_id"])["cleanup"] == "pending",
                )
        finally:
            sandbox.cleanup = original
        reconcile(settings, pending["trial_id"], launch=False)
        check("cleanup_retry_converged", finished(pending)["cleanup"] == "complete")
        check(
            "no_trial_containers_or_networks",
            all(not any(sandbox.resources(r).values()) for r in rows),
        )
        check(
            "artifact_saved",
            (execution_dir(settings, row) / "bundle/workspace/positive_sum.py").is_file(),
        )
        check(
            "one_actual_fixture_invocation",
            (execution_dir(settings, row) / "bundle/workspace/agent-started").read_text()
            == "one\n",
        )
        check(
            "events_correlated",
            all(
                all(
                    e["payload"][key] == r[key]
                    for key in ("operation_id", "attempt_id", "execution_id", "epoch")
                )
                for r in rows
                for e in store.events(r["trial_id"])
            ),
        )
    finally:
        for row in rows:
            if not finished(row):
                store.cancel(row["trial_id"])
                wait_for(
                    lambda row=row: (
                        reconcile(settings, row["trial_id"], launch=False)["cleanup"] == "complete"
                    )
                )
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=10)
        report = {
            "mode": "real-services-model-free",
            "model_calls": 0,
            "status": "passed"
            if checks and all(checks.values()) and len(checks) >= 20
            else "failed",
            "checks": checks,
            "elapsed_seconds": round(time.monotonic() - began, 2),
            "trials": [summary(store.get(r["trial_id"])) for r in rows],
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fault-worker", action="store_true")
    args = parser.parse_args()
    if args.fault_worker:
        asyncio.run(fault_worker())
    elif args.output:
        main(args.output)
    else:
        parser.error("--output is required")
