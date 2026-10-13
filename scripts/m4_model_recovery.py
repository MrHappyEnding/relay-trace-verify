"""Freeze, inject one executor loss, and audit a real Codex checkpoint recovery."""

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from rtv.benchmark.protocol import digest
from rtv.cluster.cli import configure, fixture_spec
from rtv.cluster.config import ClusterConfig, pool_label
from rtv.cluster.control import reconcile
from rtv.cluster.credentials import authorize_recovery, install_secret, remove_secret
from rtv.cluster.kubernetes import Kubernetes
from rtv.cluster.recovery import prepare_run, validate_run
from rtv.cluster.report import export_report
from rtv.cluster.store import ClusterStore
from rtv.runtime.config import REPO, TERMINAL, Settings, execution_dir
from rtv.runtime.relay_store import RelayStore
from rtv.runtime.store import Fenced
from rtv.runtime.tasks import workspace_digest


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
    path.chmod(0o600)


def source_hashes():
    paths = [*sorted((REPO / "src/rtv").rglob("*.py")), Path(__file__).resolve()]
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def changed_paths(workspace, base_commit):
    """Read changes without refreshing the export's Git index."""
    return subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            f"safe.directory={workspace}",
            "diff",
            "--name-only",
            base_commit,
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def prepare(args):
    config = ClusterConfig(**json.loads(args.config.read_text()))
    if config.model_access or config.max_jobs != 1:
        raise ValueError("Use an uncredentialed single-job repository pool")
    if config.context != "k3d-rtv-m4" or config.namespace != "rtv-m4":
        raise ValueError("Fault injection only supports the owned local development cluster")
    root = args.root.resolve()
    config.validate_root(Settings(root, config.control_database_url, "", schema="rtv4"))
    if root.exists() or args.output.exists():
        raise ValueError("Use fresh output paths; recovery runs never overwrite prior evidence")
    source = fixture_spec(
        task_contract=args.contract,
        scripted_controls=None,
        adapter="codex",
        allow_model_calls=True,
        agent_seconds=300,
        ttl=1800,
        staged=True,
        pause_after_checkpoint=True,
    )
    if source["task_contract"]["contract_sha256"] != config.repository_contract_sha256:
        raise ValueError("Task differs from the prepared pool")
    run = prepare_run(source, root)
    body = {
        "config": str(args.config.resolve()),
        "pool": config.frozen(),
        "run": run,
        "source_sha256": source_hashes(),
        "budget": {
            "maximum_codex_subscription_stages": 3,
            "maximum_api_requests": 0,
            "api_cost_usd": 0,
            "subscription_cost_usd": None,
            "maximum_recoveries": 1,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write(args.output, {**body, "prepared_sha256": digest(body)})
    print(json.dumps({"prepared": str(args.output), "budget": body["budget"], "model_calls": 0}))


def execute(args):
    if not args.allow_model_calls:
        raise ValueError("Real Codex recovery requires explicit model opt-in")
    prepared = json.loads(args.prepared.read_text())
    body = {k: v for k, v in prepared.items() if k != "prepared_sha256"}
    if digest(body) != prepared["prepared_sha256"] or prepared["source_sha256"] != source_hashes():
        raise ValueError("Frozen plan or executing source changed")
    run = prepared["run"]
    validate_run(run)
    config = ClusterConfig(**json.loads(Path(prepared["config"]).read_text()))
    if config.frozen() != prepared["pool"] or config.model_access:
        raise ValueError("Frozen pool changed")
    if config.context != "k3d-rtv-m4" or config.namespace != "rtv-m4":
        raise ValueError("Fault target is outside the owned local cluster")
    output = Path(run["root"])
    output.mkdir(parents=True, exist_ok=False)
    write(output / "prepared.json", prepared)
    kube = Kubernetes(config)
    refs, rows, checks, errors, fault = {}, [], {}, [], {}
    worker = store = settings = None
    started, completed = time.monotonic(), False

    def check(name, value):
        checks[name] = bool(value)
        print(json.dumps({"check": name, "passed": bool(value)}), flush=True)
        if not value:
            raise AssertionError(name)

    def wait(fn, seconds=1200):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if value := fn():
                return value
            if worker and worker.poll() is not None:
                raise RuntimeError("Control worker exited")
            time.sleep(0.5)
        raise TimeoutError("Recovery acceptance did not converge")

    def finished(trial_id):
        current = store.get(trial_id)
        return (
            current
            if current["status"] in TERMINAL | {"sealed"} and current["cleanup"] == "complete"
            else None
        )

    try:
        write(
            output / "credential-intent.json",
            {"name": f"rtv-model-{run['run_sha256'][:24]}-codex", "run_sha256": run["run_sha256"]},
        )
        refs["codex"] = install_secret(kube, run["run_sha256"], "codex", args.auth_file)
        write(output / "credential-references.json", refs)
        access = authorize_recovery(run, refs)
        config = replace(config, model_access=access)
        config_path = output / "config.json"
        write(config_path, asdict(config))
        settings, config = configure(config_path, output)
        store, relay = ClusterStore(settings), RelayStore(settings)
        store.initialize_pool(config)
        with (output / "worker.log").open("wb") as log:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "rtv",
                    "cluster",
                    "--config",
                    str(config_path),
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
        check("source_sealed", source["status"] == "sealed")
        check(
            "source_not_given_hidden_verifier",
            source["result"]["verification"]["status"] == "not_run",
        )
        check("source_native_session_recorded", bool(source["result"].get("session_id")))
        source_dir = execution_dir(settings, source) / "bundle/workspace"
        host_source_digest = workspace_digest(source_dir, source["spec"])
        cp = store.checkpoint(source["latest_checkpoint"])
        # Docker Desktop exposes some read-only Git objects as 0644 on macOS.
        # Compare runtime restore events with the committed Linux-side digest;
        # use the host view only for before/after source immutability.
        source_digest = cp["context"]["workspace_digest"]
        patch = changed_paths(source_dir, source["spec"]["task_contract"]["base_commit"])
        check("checkpoint_contains_real_code_changes", bool(patch))
        entry = run["entries"][0]
        eid = relay.create_experiment(
            source,
            entry["key"],
            [{"label": entry["label"], "policy": entry["policy"], "spec": entry["spec"]}],
            1800,
        )
        target = relay.experiment(eid)["trials"][0]
        rows.append(target)
        check("target_identity_matches_frozen_plan", target["trial_id"] == entry["trial_id"])

        def inflight():
            current = store.get(target["trial_id"])
            if current["status"] in TERMINAL:
                raise RuntimeError("Native continuation completed before fault injection")
            if current["epoch"] != 1 or not store.ledger(current["trial_id"])["pending_operations"]:
                return None
            allocation = store.allocation(current["execution_id"])
            pods = kube.pods(allocation) if allocation else []
            if len(pods) != 1 or not allocation["job_uid"] or not allocation["node_name"]:
                return None
            project = "rtv-m2-" + current["execution_id"].replace("-", "")
            # Emit only a boolean; exclude the short `codex --version` preflight.
            probe_code = (
                "import pathlib,sys\n"
                "for p in pathlib.Path('/proc').glob('[0-9]*/comm'):\n"
                " try:\n"
                "  if p.read_text().strip() != 'codex': continue\n"
                "  args=p.with_name('cmdline').read_bytes().split(b'\\0')\n"
                "  if b'exec' in args and b'resume' in args: sys.exit(0)\n"
                " except OSError: pass\n"
                "sys.exit(1)\n"
            )
            command = (
                "cid=$(docker ps -q --filter label=com.docker.compose.project="
                + project
                + " --filter label=com.docker.compose.service=main); "
                'test -n "$cid" && docker exec "$cid" python -c ' + shlex.quote(probe_code)
            )
            probe = subprocess.run(
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
            return current if probe.returncode == 0 else None

        old = wait(inflight)
        allocation = store.allocation(old["execution_id"])
        check("native_codex_process_observed_before_fault", True)
        check(
            "first_execution_restored_checkpoint",
            any(
                e["kind"] == "workspace.restored"
                and e["payload"]["execution_id"] == old["execution_id"]
                and e["payload"]["workspace_digest"] == source_digest
                for e in store.events(target["trial_id"])
            ),
        )
        fault.update(
            kind=run["fault"],
            at=datetime.now(UTC),
            old_execution=old["execution_id"],
            old_node=allocation["node_name"],
            job_name=allocation["job_name"],
            job_uid=allocation["job_uid"],
            checkpoint=cp["checkpoint_id"],
            checkpoint_manifest=cp["manifest_sha256"],
            changed_files=patch,
        )
        write(output / "fault.json", fault)
        wait(lambda: kube.remove(allocation, pool_label(settings)), seconds=90)
        new = wait(
            lambda: r if (r := store.get(target["trial_id"]))["epoch"] == 2 else None, seconds=120
        )
        check("recovery_uses_committed_source", new["resume_from"] == cp["checkpoint_id"])
        check("absolute_deadline_preserved", new["deadline"] == old["deadline"])
        check("attempt_identity_preserved", new["attempt_id"] == old["attempt_id"])
        check("exactly_one_recovery", new["recoveries"] == 1)
        try:
            store.event(old, "acceptance.stale-execution", {})
        except Fenced:
            check("old_execution_cannot_publish", True)
        else:
            check("old_execution_cannot_publish", False)
        result = wait(lambda: finished(target["trial_id"]))
        new_allocation = store.allocation(result["execution_id"])
        events = store.events(target["trial_id"])
        restored = [
            e
            for e in events
            if e["kind"] == "workspace.restored"
            and e["payload"]["execution_id"] == result["execution_id"]
        ]
        check(
            "recovered_workspace_matches_checkpoint",
            len(restored) == 1 and restored[0]["payload"]["workspace_digest"] == source_digest,
        )
        check("recovery_runs_on_other_node", new_allocation["node_name"] != allocation["node_name"])
        check(
            "native_session_preserved",
            result["result"].get("session_id") == source["result"]["session_id"],
        )
        check(
            "independent_verifier_completed",
            result["result"].get("verification", {}).get("status") == "completed"
            and result["result"]["verification"].get("isolated") is True,
        )
        check(
            "source_snapshot_unchanged",
            workspace_digest(source_dir, source["spec"]) == host_source_digest
            and store.checkpoint(cp["checkpoint_id"])["manifest_sha256"] == cp["manifest_sha256"],
        )
        check(
            "lost_stage_usage_retained_as_unknown",
            store.ledger(target["trial_id"])["unknown_operations"] == 1,
        )
        check("only_two_target_executions", len(store.executions(target["trial_id"])) == 2)
        fault.update(
            new_execution=result["execution_id"],
            new_node=new_allocation["node_name"],
            restored_at=restored[0]["at"],
            fault_to_restore_seconds=(restored[0]["at"] - fault["at"]).total_seconds(),
            verifier_reward=result["result"].get("rewards", {}).get("reward"),
            task_status=result["status"],
        )
        # Task correctness is reported separately from recovery invariants.
        completed = True
    except Exception as exc:
        errors.append(
            {
                "type": type(exc).__name__,
                "check": str(exc) if isinstance(exc, AssertionError) else None,
            }
        )
    finally:
        if worker:
            worker.terminate()
            try:
                worker.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
            worker = None
        if store:
            for current in rows:
                try:
                    if store.get(current["trial_id"])["status"] not in TERMINAL:
                        store.cancel(current["trial_id"])
                    wait(
                        lambda current=current: (
                            reconcile(settings, current["trial_id"], launch=False),
                            finished(current["trial_id"]),
                        )[1],
                        seconds=120,
                    )
                except Exception as exc:
                    errors.append({"type": "cleanup:" + type(exc).__name__})
            checks["all_owned_jobs_removed"] = all(
                a["state"] == "released" and kube.job(a["job_name"]) is None and not kube.pods(a)
                for a in store.pool_status()
            )
            try:
                export_report(settings, config, output / "runtime-report")
            except Exception as exc:
                errors.append({"type": "report:" + type(exc).__name__})
        for ref in refs.values():
            try:
                remove_secret(kube, {"run_sha256": run["run_sha256"]}, ref)
            except Exception as exc:
                errors.append({"type": "secret-cleanup:" + type(exc).__name__})
        remaining = kube.core.list_namespaced_secret(
            config.namespace,
            label_selector="rtv.dev/credential-run=" + run["run_sha256"][:63],
            _request_timeout=(3, 10),
        ).items
        checks["owned_credentials_removed"] = not remaining
        operations = (
            [o for r in rows for o in store.ledger(r["trial_id"])["operations"]] if store else []
        )
        checks["no_metered_api_calls"] = not any(o["kind"] == "mini-api" for o in operations)
        checks["bounded_subscription_stages"] = sum(o["kind"] == "codex" for o in operations) <= 3
        write(output / "fault.json", fault)
        evidence = {
            "status": "passed" if completed and all(checks.values()) and not errors else "failed",
            "mode": "real-model-recovery",
            "run_sha256": run["run_sha256"],
            "checks": checks,
            "errors": errors,
            "fault": fault,
            "api_requests": 0,
            "api_cost_usd": 0,
            "subscription_cost_usd": None,
            "codex_stages": sum(o["kind"] == "codex" for o in operations),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "runner_image": config.runner_image,
            "source_sha256": prepared["source_sha256"],
        }
        write(output / "report.json", evidence)
        kube.close()
        print(
            json.dumps({k: v for k, v in evidence.items() if k != "source_sha256"}, default=str),
            flush=True,
        )
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    for name in ("config", "contract", "root", "output"):
        prep.add_argument("--" + name, required=True, type=Path)
    launch = sub.add_parser("execute")
    launch.add_argument("--prepared", required=True, type=Path)
    launch.add_argument("--auth-file", required=True, type=Path)
    launch.add_argument("--allow-model-calls", action="store_true")
    args = parser.parse_args()
    raise SystemExit(prepare(args) if args.command == "prepare" else execute(args))
