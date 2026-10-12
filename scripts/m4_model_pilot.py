"""Prepare without credentials; execute one immutable pilot only after budget approval."""

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

from rtv.benchmark.execution import collect, prepare_pair, validate_run
from rtv.benchmark.protocol import DEFAULT_PLAN, digest, load
from rtv.cluster.cli import configure, fixture_spec
from rtv.cluster.config import ClusterConfig
from rtv.cluster.control import reconcile
from rtv.cluster.credentials import authorize, install_secret, remove_secret
from rtv.cluster.kubernetes import Kubernetes
from rtv.cluster.store import ClusterStore
from rtv.mini_agent import MAX_OUTPUT, REQUEST_BYTES, RESERVATION_USD
from rtv.runtime.config import TERMINAL, Settings
from rtv.runtime.relay_store import RelayStore


def write(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
    path.chmod(0o600)


def prepare(args):
    config = ClusterConfig(**json.loads(args.config.read_text()))
    if config.model_access or config.max_jobs != 1:
        raise ValueError("Prepare from an uncredentialed single-job repository pool")
    root = args.root.resolve()
    config.validate_root(Settings(root, config.control_database_url, "", schema="rtv4"))
    if root.exists() or args.output.exists():
        raise ValueError("Use fresh plan and runtime paths; never overwrite or retry a pilot")
    spec = fixture_spec(
        task_contract=args.contract,
        scripted_controls=None,
        adapter="codex",
        allow_model_calls=True,
        agent_seconds=300,
        ttl=1800,
        staged=True,
        pause_after_checkpoint=True,
    )
    if spec["task_contract"]["contract_sha256"] != config.repository_contract_sha256:
        raise ValueError("Prepared task does not match the executor pool")
    run = prepare_pair(load(args.protocol), spec, root, mode="real-model")
    body = {
        "schema_version": 1,
        "config": str(args.config.resolve()),
        "pool": config.frozen(),
        "protocol": str(args.protocol.resolve()),
        "run": run,
        "budget": {
            "codex_subscription_stages": 2,
            "maximum_api_requests": 32,
            "request_input_bytes": REQUEST_BYTES,
            "request_output_tokens": MAX_OUTPUT,
            "reservation_per_request_usd": RESERVATION_USD,
            "total_api_reservation_usd": round(32 * RESERVATION_USD, 9),
            "suggested_approval_usd": 2,
            "automatic_retries": 0,
            "price_source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini",
            "price_checked_on": "2026-10-12",
            "invoice_guarantee": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write(args.output, {**body, "prepared_sha256": digest(body)})
    print(json.dumps({"prepared": str(args.output), "budget": body["budget"], "model_calls": 0}))


def execute(args):
    if not args.allow_model_calls or not args.allow_api_calls:
        raise ValueError("Both model opt-ins require owner approval before executing")
    prepared = json.loads(args.prepared.read_text())
    body = {k: v for k, v in prepared.items() if k != "prepared_sha256"}
    if digest(body) != prepared["prepared_sha256"]:
        raise ValueError("Prepared run changed")
    run = prepared["run"]
    protocol = load(Path(prepared["protocol"]))
    validate_run(protocol, run)
    config = ClusterConfig(**json.loads(Path(prepared["config"]).read_text()))
    if config.frozen() != prepared["pool"] or config.model_access:
        raise ValueError("Prepared pool changed or was already credentialed")
    # Validate approval before touching real credential files or Kubernetes Secrets.
    dummy = {a: {"name": f"rtv-model-pending-{a}", "uid": "pending"} for a in ("codex", "mini")}
    authorize(run, args.approved_budget_usd, dummy)
    output = Path(run["root"])
    output.mkdir(parents=True, exist_ok=False)
    refs, rows, checks, errors = {}, [], {}, []
    settings = store = relay = worker = source = None
    kube = Kubernetes(config)
    started = time.monotonic()
    completed = False
    write(output / "run.json", run)
    write(output / "prepared.json", prepared)

    def wait(fn, seconds=1800):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = fn()
            if value:
                return value
            if worker and worker.poll() is not None:
                raise RuntimeError("Cluster worker exited; no automatic restart")
            time.sleep(1)
        raise TimeoutError("Pilot did not converge; no automatic retry")

    def finished(trial_id):
        current = store.get(trial_id)
        return (
            current
            if current["status"] in TERMINAL | {"sealed"} and current["cleanup"] == "complete"
            else None
        )

    try:
        for adapter, path in (("codex", args.auth_file), ("mini", args.api_key_file)):
            # Retain metadata-only cleanup hints even if both create and read lose their ack.
            write(
                output / "credential-intent.json",
                {
                    "run_sha256": run["run_sha256"],
                    "namespace": config.namespace,
                    "names": [f"rtv-model-{run['run_sha256'][:24]}-{a}" for a in ("codex", "mini")],
                },
            )
            refs[adapter] = install_secret(kube, run["run_sha256"], adapter, path)
            write(output / "credential-references.json", refs)
        access = authorize(run, args.approved_budget_usd, refs)
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
        checks["source_sealed_without_hidden_verifier"] = (
            source["status"] == "sealed"
            and source["result"].get("verification", {}).get("status") == "not_run"
        )
        for entry in run["entries"]:
            if entry["branch"] == "B-fresh":
                current = store.submit(entry["key"], entry["spec"])
            elif not checks["source_sealed_without_hidden_verifier"]:
                continue  # Collector records source_failed; fresh still runs in frozen order.
            else:
                eid = relay.create_experiment(
                    source,
                    entry["key"],
                    [{"label": entry["branch"], "policy": entry["policy"], "spec": entry["spec"]}],
                    1800,
                )
                current = relay.experiment(eid)["trials"][0]
            rows.append(current)
            if current["trial_id"] != entry["trial_id"]:
                raise ValueError("Submitted trial differs from the preassigned identity")
            current = wait(lambda current=current: finished(current["trial_id"]))
            checks[entry["branch"] + "_verifier_completed"] = (
                current["result"].get("verification", {}).get("status") == "completed"
                and current["result"]["verification"].get("isolated") is True
            )  # An honest unsolved result is still a completed verification.
            if entry["branch"] == "A-continue":
                checks["native_session_preserved"] = bool(source["result"].get("session_id")) and (
                    source["result"]["session_id"] == current["result"].get("session_id")
                )
            if entry["branch"] != "B-fresh":
                checks[entry["branch"] + "_prefix_restored"] = any(
                    event["kind"] == "workspace.restored"
                    and event["payload"].get("workspace_digest")
                    == source["result"].get("workspace_digest")
                    for event in store.events(current["trial_id"])
                )
        completed = True
    except Exception as exc:
        # Do not print raw authentication/provider exceptions or their context.
        errors.append(type(exc).__name__)
    finally:
        if worker:
            worker.terminate()
            try:
                worker.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
            worker = None
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
                errors.append("cleanup:" + type(exc).__name__)
        for ref in refs.values():
            try:
                remove_secret(kube, {"run_sha256": run["run_sha256"]}, ref)
            except Exception as exc:
                errors.append("secret-cleanup:" + type(exc).__name__)
        if store:
            checks["all_allocations_released"] = all(
                a["state"] == "released" for a in store.pool_status()
            )
            try:
                report = collect(protocol, run, store, output / "observations")
                checks["four_observations_linked"] = report["received"] == 4
            except Exception as exc:
                errors.append("collection:" + type(exc).__name__)
        operations = [o for r in rows for o in store.ledger(r["trial_id"])["operations"]]
        api_ops = [o for o in operations if o["kind"] == "mini-api"]
        reserved = round(sum(o["reserved_usd"] for o in api_ops), 9)
        unknown = sum(o["state"] != "known" for o in api_ops)
        checks["api_reservations_within_approval"] = reserved <= args.approved_budget_usd
        checks["no_api_request_replay"] = len(api_ops) <= 32
        evidence = {
            "status": "passed" if completed and all(checks.values()) and not errors else "failed",
            "mode": "real-model-pilot",
            "run_sha256": run["run_sha256"],
            "checks": checks,
            "errors": errors,
            "api_requests": len(api_ops),
            "codex_stages": sum(o["kind"] == "codex" for o in operations),
            "subscription_cost_usd": None,
            "unknown_api_requests": unknown,
            "api_reserved_usd": reserved,
            "known_api_cost_usd": sum(o["cost_usd"] or 0 for o in api_ops),
            "api_cost_usd": None if unknown else sum(o["cost_usd"] or 0 for o in api_ops),
            "approved_budget_usd": args.approved_budget_usd,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "limit": "One pilot; scaffold and model both change; no superiority conclusion.",
        }
        write(output / "report.json", evidence)
        kube.close()
        print(json.dumps(evidence, indent=2))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    for name in ("config", "contract", "root", "output"):
        prep.add_argument("--" + name, type=Path, required=True)
    prep.add_argument("--protocol", type=Path, default=DEFAULT_PLAN)
    launch = sub.add_parser("run")
    for name in ("prepared", "auth-file", "api-key-file"):
        launch.add_argument("--" + name, type=Path, required=True)
    launch.add_argument("--allow-model-calls", action="store_true")
    launch.add_argument("--allow-api-calls", action="store_true")
    launch.add_argument("--approved-budget-usd", type=float, required=True)
    args = parser.parse_args()
    sys.exit(execute(args) if args.command == "run" else prepare(args))
