"""Real local Kubernetes/Temporal/PostgreSQL faults with deterministic agents only."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Replayer

from rtv.cluster.cli import configure, fixture_spec
from rtv.cluster.kubernetes import Kubernetes
from rtv.cluster.report import export_report
from rtv.cluster.store import ClusterStore
from rtv.harbor_probe import tree_digest
from rtv.runtime.config import TERMINAL, execution_dir
from rtv.runtime.store import Fenced
from rtv.runtime.workflow import TrialWorkflow


def main(config_path, output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings, config = configure(config_path, output)
    if config.context != "k3d-rtv-m4" or config.namespace != "rtv-m4":
        raise ValueError("Fault injection only targets the dedicated local rtv-m4 cluster")
    store = ClusterStore(settings)
    store.initialize_pool(config)
    kube = Kubernetes(config)
    workers, rows, stopped_nodes, checks, faults = [], [], [], {}, []
    samples = []
    started = time.monotonic()
    outcome = "failed"

    def save(path, value):
        path.write_text(json.dumps(value, indent=2, default=str) + "\n")

    def check(name, condition):
        checks[name] = bool(condition)
        print(json.dumps({"check": name, "passed": bool(condition)}), flush=True)
        if not condition:
            raise AssertionError(name)

    def current(row):
        return store.get(row["trial_id"])

    def event(row, kind):
        return next(
            (
                e
                for e in store.events(row["trial_id"])
                if e["kind"] == kind and e["payload"]["execution_id"] == row["execution_id"]
            ),
            None,
        )

    def sample():
        allocations = store.pool_status()
        active = [a for a in allocations if a["state"] != "released"]
        samples.append(
            {
                "at": datetime.now(UTC),
                "held_jobs": len(active),
                "held_cpu_millis": sum(a["cpu_millis"] for a in active),
                "held_memory_mb": sum(a["memory_mb"] for a in active),
            }
        )
        if len(active) > config.max_jobs:
            raise AssertionError("Durable capacity exceeded")

    def wait(fn, timeout=150):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            sample()
            if result := fn():
                return result
            time.sleep(0.4)
        statuses = [(r["trial_id"], current(r)["status"], current(r)["epoch"]) for r in rows]
        raise TimeoutError(str(statuses))

    def submit(key, **kwargs):
        row = store.submit(key, fixture_spec(**kwargs))
        rows.append(row)
        return row

    def spawn():
        with (output / f"worker-{len(workers)}.log").open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "rtv",
                    "cluster",
                    "--config",
                    str(config_path.resolve()),
                    "--root",
                    str(output),
                    "worker",
                ],
                stdout=log,
                stderr=log,
            )
        workers.append(process)
        return process

    def kill(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    def cleaned(row):
        r = current(row)
        return r if r["status"] in TERMINAL and r["cleanup"] == "complete" else None

    def all_gone():
        return all(
            a["state"] == "released" and kube.job(a["job_name"]) is None and not kube.pods(a)
            for a in store.pool_status()
        )

    def reject(name, fn):
        try:
            fn()
        except Fenced:
            check(name, True)
        else:
            check(name, False)

    frozen = {
        "scope": "local Kubernetes reliability; deterministic Python fixture, no model API",
        "paid_api_calls": 0,
        "api_budget_usd": 0,
        "config": config.frozen(),
        "cases": [
            "concurrent idempotency",
            "capacity/queued cancellation",
            "worker SIGKILL",
            "running cancellation",
            "TTL",
            "checkpoint + executor node SIGKILL",
            "stale epoch",
            "UNKNOWN ledger",
            "Temporal replay",
            "report completeness",
        ],
        "sample_policy": "one scripted run per case; retain failures; no efficacy inference",
        "created_at": datetime.now(UTC),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "fixture_spec": fixture_spec(),
    }
    frozen["source_sha256"] = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for base in (Path("src/rtv"), Path("scripts"))
        for p in sorted(base.rglob("*.py"))
    }
    save(output / "frozen-plan.json", frozen)
    try:
        try:
            store.verify_pool(replace(config, max_jobs=config.max_jobs + 1))
        except ValueError:
            check("changed_pool_configuration_rejected", True)
        else:
            check("changed_pool_configuration_rejected", False)
        worker1, worker2 = spawn(), spawn()
        spec = fixture_spec(delay=35)
        with ThreadPoolExecutor(max_workers=6) as pool:
            duplicates = list(pool.map(lambda _: store.submit("same-request", spec), range(6)))
        first = duplicates[0]
        rows.append(first)
        check(
            "concurrent_submission_one_identity", len({r["execution_id"] for r in duplicates}) == 1
        )
        second = submit("second-slot", delay=35)
        wait(
            lambda: (
                event(current(first), "agent.started") and event(current(second), "agent.started")
            )
        )
        allocations = store.pool_status()
        check("two_executor_nodes_used", len({a["node_name"] for a in allocations}) == 2)
        check("pool_held_at_capacity", sum(a["state"] != "released" for a in allocations) == 2)
        third = submit("queued-cancel")
        time.sleep(3)
        check(
            "third_trial_waits_without_allocation", store.allocation(third["execution_id"]) is None
        )
        store.cancel(third["trial_id"])
        wait(lambda: cleaned(third))
        check(
            "queued_cancel_no_agent_invocation", not store.ledger(third["trial_id"])["operations"]
        )
        originals = {
            r["trial_id"]: store.allocation(r["execution_id"])["job_uid"] for r in (first, second)
        }
        kill(worker1)
        kill(worker2)
        faults.append({"kind": "all_control_workers_sigkill", "at": datetime.now(UTC)})
        spawn()
        spawn()
        for row in (first, second):
            final = wait(lambda row=row: cleaned(row))
            check(
                "worker_restart_same_execution_" + row["trial_id"][:8],
                final["status"] == "succeeded"
                and final["execution_id"] == row["execution_id"]
                and store.allocation(row["execution_id"])["job_uid"] == originals[row["trial_id"]]
                and len(store.ledger(row["trial_id"])["operations"]) == 1,
            )
        check("initial_jobs_and_pods_removed", all_gone())

        cancelled = submit("running-cancel", delay=60)
        wait(lambda: event(current(cancelled), "agent.started"))
        store.cancel(cancelled["trial_id"])
        check("running_cancel_cleanup", wait(lambda: cleaned(cancelled))["status"] == "cancelled")
        ttl = submit("ttl-expiry", ttl=25, delay=60)
        ended = wait(lambda: cleaned(ttl))
        check(
            "ttl_failure_with_cleanup",
            ended["status"] == "failed" and ended["result"]["reason"] == "ttl_expired",
        )

        recovery = submit("node-loss", staged=True, delay=60, ttl=600, max_recoveries=1)
        old = wait(
            lambda: (
                r if (r := current(recovery))["epoch"] == 2 and event(r, "agent.started") else None
            )
        )
        cp = store.checkpoint(old["resume_from"])
        old_allocation = store.allocation(old["execution_id"])
        node = old_allocation["node_name"]
        if node not in {"k3d-rtv-m4-agent-0", "k3d-rtv-m4-agent-1"}:
            raise ValueError("Refusing to stop a node not owned by this acceptance cluster")
        fault_time = datetime.now(UTC)
        stopped_nodes.append(node)
        subprocess.run(
            ["docker", "kill", "--signal", "KILL", node], check=True, capture_output=True
        )
        faults.append(
            {
                "kind": "executor_node_sigkill",
                "at": fault_time,
                "node": node,
                "execution_id": old["execution_id"],
            }
        )
        new = wait(lambda: r if (r := current(recovery))["epoch"] == 3 else None, timeout=90)
        check(
            "node_loss_new_epoch_same_attempt",
            new["attempt_id"] == old["attempt_id"] and new["execution_id"] != old["execution_id"],
        )
        check("recovery_preserves_absolute_deadline", new["deadline"] == old["deadline"])
        check(
            "unavailable_node_reservation_not_released",
            store.allocation(old["execution_id"])["state"] != "released",
        )
        check(
            "interrupted_call_kept_unknown",
            store.ledger(recovery["trial_id"])["unknown_operations"] == 1,
        )
        reject("stale_epoch_cannot_change_phase", lambda: store.phase(old, "verifying", {}))
        reject(
            "stale_epoch_cannot_finish",
            lambda: store.finish(old, {"status": "failed", "reason": "stale"}),
        )
        wait(lambda: event(current(recovery), "workspace.restored"), timeout=120)
        new = current(recovery)
        check(
            "recovery_runs_on_other_node",
            store.allocation(new["execution_id"])["node_name"] != node,
        )
        roundtrip = execution_dir(settings, new) / "roundtrip"
        check(
            "restored_workspace_matches_committed_digest",
            tree_digest(roundtrip) == cp["context"]["workspace_digest"],
        )
        check("uncommitted_files_discarded", not (roundtrip / "uncommitted.txt").exists())
        faults[-1]["restored_at"] = datetime.now(UTC)
        faults[-1]["fault_to_restore_seconds"] = (
            faults[-1]["restored_at"] - fault_time
        ).total_seconds()
        subprocess.run(["docker", "start", node], check=True, capture_output=True)
        stopped_nodes.remove(node)
        final = wait(lambda: cleaned(recovery), timeout=180)
        check(
            "node_recovery_verified",
            final["status"] == "succeeded"
            and final["epoch"] == 3
            and final["result"]["rewards"] == {"reward": 1.0},
        )
        check(
            "source_checkpoint_immutable",
            store.checkpoint(cp["checkpoint_id"])["manifest_sha256"] == cp["manifest_sha256"],
        )
        wait(all_gone)
        check("all_allocations_jobs_pods_reclaimed", all_gone())
        check("capacity_never_exceeded", max(s["held_jobs"] for s in samples) <= config.max_jobs)

        async def replay():
            client = await Client.connect(settings.temporal)
            handle = client.get_workflow_handle("rtv4-" + recovery["trial_id"])
            result = await asyncio.wait_for(handle.result(), 45)
            history = await handle.fetch_history()
            await Replayer(workflows=[TrialWorkflow]).replay_workflow(history)
            (output / "temporal-history.json").write_text(history.to_json())
            return result

        check("temporal_history_replays", asyncio.run(replay())["epoch"] == 3)
        incomplete = submit("visible-incomplete", staged=True, pause_after_checkpoint=True)
        wait(
            lambda: (
                current(incomplete)["status"] == "sealed"
                and current(incomplete)["cleanup"] == "complete"
            )
        )
        report = export_report(settings, config, output / "snapshot-with-incomplete")
        check(
            "report_keeps_failed_cancelled_and_incomplete",
            report["incomplete_count"] == 1
            and {"failed", "cancelled", "succeeded", "sealed"}
            <= {r["status"] for r in report["trials"]},
        )
        check(
            "raw_records_match_report_hashes",
            all(
                hashlib.sha256((output / "snapshot-with-incomplete" / p).read_bytes()).hexdigest()
                == h
                for p, h in report["sha256"].items()
            ),
        )
        store.cancel(incomplete["trial_id"])
        wait(lambda: cleaned(incomplete))
        wait(all_gone)
        outcome = "passed"
    finally:
        for node in stopped_nodes:
            subprocess.run(["docker", "start", node], check=False, capture_output=True)
        if outcome != "passed":
            for row in rows:
                if current(row)["status"] not in TERMINAL:
                    store.cancel(row["trial_id"])
            # Leave durable cleanup intent and give live reconcilers a bounded drain.
            try:
                wait(all_gone, timeout=45)
            except TimeoutError:
                pass
        for process in workers:
            kill(process)
        kube.close()
        save(output / "faults.json", faults)
        save(output / "capacity-samples.json", samples)
        report = export_report(settings, config, output / "final-report")
        evidence = {
            "status": outcome,
            "mode": "real-local-kubernetes-scripted-agent",
            "paid_api_calls": 0,
            "checks": checks,
            "faults": faults,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "config": config.frozen(),
            "trial_count": report["trial_count"],
            "known_api_cost_usd": report["known_api_cost_usd"],
            "unknown_operations": report["unknown_operations"],
            "limitations": report["limitations"],
            "raw_report_sha256": hashlib.sha256(
                (output / "final-report/report.json").read_bytes()
            ).hexdigest(),
        }
        save(output / "acceptance.json", evidence)
        print(json.dumps(evidence, indent=2, default=str), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    main(args.config, args.output)
