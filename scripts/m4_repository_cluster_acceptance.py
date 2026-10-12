"""Local Kubernetes repository pairing and a separate fault trial; no model calls."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from rtv.benchmark.execution import collect, prepare_pair
from rtv.benchmark.protocol import DEFAULT_PLAN, load
from rtv.cluster.cli import configure, fixture_spec
from rtv.cluster.config import pool_label
from rtv.cluster.control import reconcile
from rtv.cluster.kubernetes import Kubernetes
from rtv.cluster.store import ClusterStore
from rtv.runtime.config import TERMINAL, execution_dir
from rtv.runtime.relay_store import RelayStore
from rtv.runtime.tasks import validate_contract, workspace_digest


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings, config = configure(args.config, output)
    if config.context != "k3d-rtv-m4" or config.namespace != "rtv-m4":
        raise ValueError("Fault tests only target the owned local development cluster")
    for key in (
        "RTV_ALLOW_MODEL_CALLS",
        "RTV_ALLOW_API_CALLS",
        "RTV_CODEX_AUTH_FILE",
        "RTV_API_KEY_FILE",
    ):
        os.environ.pop(key, None)
    store, relay, kube = ClusterStore(settings), RelayStore(settings), Kubernetes(config)
    store.initialize_pool(config)
    declared = validate_contract(json.loads(args.contract.read_text()))
    if declared["instance_id"] != "sympy__sympy-22914":
        raise ValueError("Scripted controls are pinned to the first pilot task")
    oracle = (Path(declared["task_directory"]) / "solution/solve.sh").read_text()
    controls = output / "controls.json"
    controls.write_text(
        json.dumps(
            {
                "scripted_source": [
                    "test ! -e /tests/test.sh && test ! -e /solution/solve.sh",
                    "mkdir .rtv-state && chmod 750 .rtv-state "
                    "&& printf preserved > .rtv-state/note",
                ],
                "scripted_target": [
                    "test ! -e /tests/test.sh && test ! -e /solution/solve.sh",
                    "bash -c " + shlex.quote(oracle),
                ],
            }
        )
    )
    spec = fixture_spec(
        task_contract=args.contract,
        scripted_controls=controls,
        agent_seconds=300,
        ttl=1800,
        staged=True,
        pause_after_checkpoint=True,
    )
    protocol = load(args.plan)
    run = prepare_pair(protocol, spec, output)
    (output / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    rows, checks, pod_snapshots = [], {}, {}
    worker = None
    started = time.monotonic()
    completed = False

    def check(name, value):
        checks[name] = bool(value)
        print(json.dumps({"check": name, "passed": bool(value)}), flush=True)
        if not value:
            raise AssertionError(name)

    def wait(fn, seconds=900):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            for allocation in store.pool_status():
                if allocation["state"] != "released":
                    pods = kube.pods(allocation)
                    if pods:
                        pod_snapshots[allocation["execution_id"]] = pods
            if value := fn():
                return value
            time.sleep(0.5)
        raise TimeoutError("Repository cluster acceptance did not converge")

    def finished(trial):
        row = store.get(trial)
        return (
            row if row["status"] in TERMINAL | {"sealed"} and row["cleanup"] == "complete" else None
        )

    def register(entry, source):
        if entry["branch"] == "B-fresh":
            row = store.submit(entry["key"], entry["spec"])
        else:
            eid = relay.create_experiment(
                source,
                entry["key"],
                [
                    {
                        "label": entry["branch"],
                        "policy": entry["policy"],
                        "spec": entry["spec"],
                    }
                ],
                1800,
            )
            row = relay.experiment(eid)["trials"][0]
        rows.append(row)
        check(entry["branch"] + "_preassigned_identity", row["trial_id"] == entry["trial_id"])
        return row

    try:
        with (output / "worker.log").open("wb") as log:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "rtv",
                    "cluster",
                    "--config",
                    str(args.config.resolve()),
                    "--root",
                    str(output),
                    "worker",
                ],
                stdout=log,
                stderr=log,
            )
        source = store.submit(run["source"]["key"], run["source"]["spec"])
        rows.append(source)
        source = wait(lambda: finished(source["trial_id"]))
        check(
            "source_sealed_without_verifier",
            source["status"] == "sealed"
            and source["result"]["verification"]["status"] == "not_run",
        )
        prefix_digest = source["result"]["workspace_digest"]
        host_prefix_digest = workspace_digest(
            execution_dir(settings, source) / "bundle/workspace", spec
        )
        actual_order = []
        for entry in run["entries"]:
            actual_order.append(entry["branch"])
            row = register(entry, source)
            row = wait(lambda row=row: finished(row["trial_id"]))
            check(
                entry["branch"] + "_oracle_verified",
                row["status"] == "succeeded" and row["result"]["verification"]["isolated"],
            )
            if entry["branch"] != "B-fresh":
                check(
                    entry["branch"] + "_exact_prefix",
                    workspace_digest(execution_dir(settings, row) / "restored/workspace", spec)
                    == host_prefix_digest
                    and any(
                        e["kind"] == "workspace.restored"
                        and e["payload"].get("workspace_digest") == prefix_digest
                        for e in store.events(row["trial_id"])
                    ),
                )
            else:
                check(
                    "fresh_no_prefix",
                    not row["parent_checkpoint"]
                    and not (execution_dir(settings, row) / "bundle/workspace/.rtv-state").exists(),
                )
        check("frozen_branch_order", actual_order == [e["branch"] for e in run["entries"]])
        report = collect(protocol, run, store, output / "observations")
        check("four_linked_observations", report["received"] == 4 and report["mode"] == "oracle")
        check("other_pilot_tasks_stay_missing", report["planned"] == 16 and report["missing"] == 12)
        check(
            "no_incomplete_confidence_intervals",
            all(v["ci95"] is None for v in report["comparisons"].values()),
        )
        # A separate reliability trial cannot replace a preassigned experiment observation.
        fault_spec = {
            **run["entries"][0]["spec"],
            "adapter": "fixture",
            "stages": 2,
            "max_recoveries": 1,
            "benchmark_assignment": {"role": "fault-only"},
            "scripted_target": [
                "test ! -e .inflight && touch .inflight && sleep 45 && rm .inflight",
                *spec["scripted_target"],
            ],
        }
        eid = relay.create_experiment(
            source,
            "separate-recovery-control",
            [{"label": "fault-only", "policy": "task-only", "spec": fault_spec}],
            1800,
        )
        fault = relay.experiment(eid)["trials"][0]
        rows.append(fault)

        def inflight():
            row = store.get(fault["trial_id"])
            if row["status"] in TERMINAL:
                raise RuntimeError("Fault trial finished before injection")
            if store.ledger(row["trial_id"])["pending_operations"]:
                allocation = store.allocation(row["execution_id"])
                pods = kube.pods(allocation)
                if len(pods) != 1:
                    return None
                project = "rtv-m2-" + row["execution_id"].replace("-", "")
                command = (
                    "cid=$(docker ps -q --filter label=com.docker.compose.project="
                    + project
                    + " --filter label=com.docker.compose.service=main); "
                    'test -n "$cid" && docker exec "$cid" test -f /testbed/.inflight'
                )
                checked = subprocess.run(
                    [
                        "kubectl",
                        "--kubeconfig",
                        config.kubeconfig,
                        "--context",
                        config.context,
                        "-n",
                        config.namespace,
                        "exec",
                        pods[0]["metadata"]["name"],
                        "--",
                        "sh",
                        "-c",
                        command,
                    ],
                    capture_output=True,
                    timeout=15,
                )
                if checked.returncode == 0:
                    return row
            return None

        fault = wait(inflight)
        check("uncommitted_marker_observed_before_fault", True)
        allocation = store.allocation(fault["execution_id"])
        check("fault_has_adopted_job_uid", bool(allocation["job_uid"] and allocation["node_name"]))
        wait(lambda: kube.remove(allocation, pool_label(settings)), seconds=90)
        recovered = wait(
            lambda: r if (r := store.get(fault["trial_id"]))["epoch"] > fault["epoch"] else None
        )
        check(
            "inherited_checkpoint_recovery", recovered["resume_from"] == source["latest_checkpoint"]
        )
        check("recovery_deadline_unchanged", recovered["deadline"] == fault["deadline"])
        recovered = wait(lambda: finished(fault["trial_id"]))
        check("recovered_trial_verified", recovered["status"] == "succeeded")
        next_allocation = store.allocation(recovered["execution_id"])
        check("recovery_on_other_node", next_allocation["node_name"] != allocation["node_name"])
        check(
            "unknown_operation_retained", store.ledger(fault["trial_id"])["unknown_operations"] == 1
        )
        check(
            "inflight_write_discarded",
            not (execution_dir(settings, recovered) / "restored/workspace/.inflight").exists(),
        )
        check(
            "source_snapshot_unchanged",
            workspace_digest(execution_dir(settings, source) / "bundle/workspace", spec)
            == host_prefix_digest,
        )
        check(
            "fault_not_in_paired_statistics",
            all(o.get("trial_id") != fault["trial_id"] for o in report["observations"]),
        )
        completed = True
    finally:
        if worker and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=15)
        # Preserve logs before deleting only this pool's remaining Jobs.
        for allocation in store.pool_status():
            for pod in kube.pods(allocation):
                try:
                    log = kube.core.read_namespaced_pod_log(
                        pod["metadata"]["name"],
                        config.namespace,
                        tail_lines=100,
                        _request_timeout=(3, 10),
                    )
                    (output / (allocation["execution_id"] + "-pod.log")).write_text(log)
                except Exception:
                    pass
        for row in rows:
            current = store.get(row["trial_id"])
            if current["status"] not in TERMINAL:
                store.cancel(row["trial_id"])
            wait(
                lambda row=row: (
                    reconcile(settings, row["trial_id"], launch=False),
                    finished(row["trial_id"]),
                )[1],
                seconds=120,
            )
        checks["all_owned_jobs_and_pods_removed"] = all(
            a["state"] == "released" and kube.job(a["job_name"]) is None and not kube.pods(a)
            for a in store.pool_status()
        )
        (output / "pods.json").write_text(json.dumps(pod_snapshots, indent=2, default=str) + "\n")
        evidence = {
            "status": "passed" if completed and all(checks.values()) else "failed",
            "mode": "real-local-kubernetes-oracle-controls",
            "model_calls": 0,
            "paid_api_calls": 0,
            "cloud_spend_usd": 0,
            "checks": checks,
            "pool": store.pool_status(),
            "config": config.frozen(),
            "run_sha256": run["run_sha256"],
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "not_tested": [
                "real models",
                "Codex native session",
                "credential delivery",
                "physical multi-host fault",
                "full pilot/evaluation",
            ],
        }
        (output / "report.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
        print(json.dumps({"status": evidence["status"], "checks": checks}), flush=True)
        kube.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    main(parser.parse_args())
