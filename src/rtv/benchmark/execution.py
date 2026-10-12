"""Pre-execution assignments and homogeneous runtime observations for pilot runs."""

import hashlib
import json
from pathlib import Path

from rtv.benchmark.protocol import digest, environment_protocol, matrix
from rtv.runtime.handoff import plan as handoff_plan
from rtv.runtime.relay_store import stable
from rtv.runtime.store import identity, spec_hash
from rtv.runtime.tasks import contract


def prepare_pair(protocol, source_spec, root, repeat=0, *, mode="oracle"):
    from rtv.codex_agent import DEFAULT_MODEL
    from rtv.mini_agent import MINI_MODEL, api_envelope

    envelope = api_envelope(source_spec)
    declared = contract(source_spec)
    if not declared or declared["protocol_sha256"] != environment_protocol(protocol):
        raise ValueError("Repository task does not match the frozen protocol")
    if mode not in {"oracle", "real-model"}:
        raise ValueError("Unsupported pilot evidence mode")
    real = mode == "real-model"
    prefix_policy = protocol["checkpoint"].get("prefix_policy")
    if source_spec.get("prefix_policy") not in (None, prefix_policy):
        raise ValueError("Source prefix policy differs from the frozen protocol")
    if prefix_policy is not None and not real:
        raise ValueError("Codex tool gate cannot be represented by an oracle control")
    if (
        source_spec["adapter"] != ("codex" if real else "fixture")
        or source_spec.get("max_recoveries") != 0
    ):
        raise ValueError("Pilot requires the matching source adapter without automatic retries")
    if real and (
        source_spec.get("model") != DEFAULT_MODEL
        or any(k.startswith("scripted_") for k in source_spec)
        or source_spec.get("ttl_seconds") != 1800
        or not source_spec.get("isolated_verifier")
    ):
        raise ValueError("Real pilot requires pinned Codex, isolated verification and no controls")
    schedule = [
        r
        for r in matrix(protocol, "pilot")
        if r["instance_id"] == declared["instance_id"] and r["repeat"] == repeat
    ]
    if len(schedule) != 4:
        raise ValueError("Pair is outside the frozen pilot assignment")
    root = str(Path(root).resolve())
    pair = schedule[0]["pair_id"]
    source = {
        **source_spec,
        "stages": 2,
        "pause_after_checkpoint": True,
        "agent_seconds": protocol["budget"]["prefix_seconds"],
        "api_budget_usd": 0,
        "benchmark_assignment": {"pair_id": pair, "role": "prefix", "mode": mode},
    }
    if prefix_policy is not None:
        source["prefix_policy"] = prefix_policy
    source_key = pair + ":prefix"
    target = "mini" if real else "mini-fixture"
    branches = {r["label"]: r for r in handoff_plan(source, adapter=target, ttl=1800)}
    entries = []
    for assignment in schedule:
        label = assignment["branch"]
        fresh = label == "B-fresh"
        key = pair + ":" + label
        eid = None if fresh else stable(root + ":experiment:" + key)
        spec = {
            **(source if fresh else branches[label]["spec"]),
            "adapter": target if fresh else branches[label]["spec"]["adapter"],
            "model": (MINI_MODEL if real else None) if fresh else branches[label]["spec"]["model"],
            "stages": 1 if fresh else 2,
            "pause_after_checkpoint": False,
            "max_recoveries": 0,
            "agent_seconds": protocol["budget"][
                "fresh_seconds" if fresh else "continuation_seconds"
            ],
            "api_call_limit": max(1, protocol["budget"]["api_requests"][label]),
            "api_budget_usd": round(
                protocol["budget"]["api_requests"][label] * envelope.reservation_usd, 9
            )
            if real
            else 0,
            "benchmark_assignment": {**assignment, "mode": mode},
        }
        # The gate is source-only. Native and semantic continuations get their full allowance.
        spec.pop("prefix_policy", None)
        entries.append(
            {
                **assignment,
                "key": key,
                "experiment_id": eid,
                "trial_id": identity(root, key if fresh else eid + ":" + label)["trial_id"],
                "policy": None if fresh else branches[label]["policy"],
                "spec": spec,
            }
        )
    body = {
        "schema_version": 1,
        "mode": mode,
        "split": "pilot",
        "root": root,
        "protocol_sha256": protocol["protocol_sha256"],
        "source": {
            "key": source_key,
            "trial_id": identity(root, source_key)["trial_id"],
            "spec": source,
        },
        "entries": entries,
    }
    return {**body, "run_sha256": digest(body)}


def validate_run(protocol, run):
    # Reconstruct all assignments, identities and budgets, not just a caller-supplied hash.
    expected = prepare_pair(
        protocol, run["source"]["spec"], run["root"], run["entries"][0]["repeat"], mode=run["mode"]
    )
    if expected != run:
        raise ValueError("Frozen runtime assignment changed")


def outcome(row):
    if row["status"] == "cancelled":
        return "cancelled"
    result = row.get("result") or {}
    verifier = result.get("verification") or {}
    reward = result.get("rewards", {}).get("reward")
    if verifier.get("status") == "completed" and verifier.get("isolated") and reward in (0, 1):
        return "solved" if reward == 1 else "unsolved"
    return "infra_error"


def collect(protocol, run, store, output):
    """Snapshot runtime truth; missing, failed and UNKNOWN rows are never substituted."""
    from rtv.benchmark.analysis import analyze
    from rtv.benchmark.report import export
    from rtv.cluster.report import trial_metrics
    from rtv.runtime.config import TERMINAL

    validate_run(protocol, run)
    if str(store.settings.root) != run["root"]:
        raise ValueError("Run belongs to another runtime root")
    output.mkdir(parents=True, exist_ok=False)
    (output / "run.json").write_text(json.dumps(run, indent=2) + "\n")
    hashes = {}
    metrics = {}

    def snapshot(expected):
        try:
            row = store.get(expected["trial_id"])
        except ValueError as exc:
            if str(exc) != "Unknown trial in this runtime root":
                raise
            return None
        if spec_hash(row["spec"]) != spec_hash(expected["spec"]):
            raise ValueError("Runtime spec differs from its pre-execution assignment")
        ledger = store.ledger(row["trial_id"])
        kinds = {o["kind"] for o in ledger["operations"]}
        if run["mode"] == "oracle" and kinds & {"codex", "mini-api"}:
            raise ValueError("Real model operations cannot enter an oracle report")
        if run["mode"] == "real-model":
            allowed = {"codex"} if row["spec"]["adapter"] == "codex" else {"mini", "mini-api"}
            required = "codex" if row["spec"]["adapter"] == "codex" else "mini-api"
            if kinds - allowed or (
                outcome(row) in {"solved", "unsolved"} and required not in kinds
            ):
                raise ValueError("Real report requires matching model operations, never controls")
        events = store.events(row["trial_id"])
        if expected.get("policy"):
            transfers = [e for e in events if e["kind"] == "handoff.completed"]
            if any(e["payload"].get("policy") != expected["policy"] for e in transfers) or (
                outcome(row) in {"solved", "unsolved"} and not transfers
            ):
                raise ValueError("Runtime context policy differs from its assignment")
        raw = {
            "row": row,
            "ledger": ledger,
            "events": events,
            "executions": store.executions(row["trial_id"]),
        }
        metrics[row["trial_id"]] = trial_metrics(row, events)
        raw["metrics"] = metrics[row["trial_id"]]
        path = output / (row["trial_id"] + ".json")
        path.write_text(json.dumps(raw, indent=2, default=str) + "\n")
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return row, ledger

    prefix = snapshot(run["source"])
    source_checkpoint = None
    if prefix and prefix[0]["latest_checkpoint"]:
        source_checkpoint = store.checkpoint(prefix[0]["latest_checkpoint"])
        if source_checkpoint["context"]["result"].get("status") != "sealed":
            raise ValueError("Prefix checkpoint was not sealed")
        if run["source"]["spec"].get("prefix_policy") is not None:
            boundary = source_checkpoint["context"]["result"].get("prefix_boundary", {})
            if (
                boundary.get("policy") != run["source"]["spec"]["prefix_policy"]
                or boundary.get("admitted_tools") != boundary.get("completed_tools")
                or boundary.get("trigger") not in {"tool_budget", "natural_completion"}
            ):
                raise ValueError("Sealed source is missing its audited tool boundary")
        path = output / "source-checkpoint.json"
        path.write_text(json.dumps(source_checkpoint, indent=2, default=str) + "\n")
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    records = []
    for entry in run["entries"]:
        fresh = entry["branch"] == "B-fresh"
        state = snapshot(entry)
        source_failed = (
            not fresh and prefix and not source_checkpoint and prefix[0]["status"] in TERMINAL
        )
        if state is None or state[0]["status"] not in TERMINAL:
            if not source_failed:
                continue
            row, ledger = None, {"cost_usd": 0, "known_api_cost_usd": 0, "unknown_operations": 0}
            status = "source_failed"
        else:
            row, ledger = state
            status = outcome(row)
        cp = None
        if not fresh and row is not None:
            if not source_checkpoint:
                raise ValueError("Continuation requires its sealed source")
            cp = prefix[0]["latest_checkpoint"]
            if row["parent_checkpoint"] != cp or set(ledger["prefix_operation_ids"]) != {
                o["operation_id"] for o in prefix[1]["operations"]
            }:
                raise ValueError("Runtime continuation/prefix lineage disagrees")
        elif (
            fresh
            and row is not None
            and (row["parent_checkpoint"] or ledger["prefix_operation_ids"])
        ):
            raise ValueError("Fresh baseline inherited a prefix")
        result = (row or {}).get("result") or {}
        records.append(
            {
                **{
                    k: entry[k]
                    for k in (
                        "observation_id",
                        "pair_id",
                        "instance_id",
                        "repeat",
                        "split",
                        "branch",
                        "order",
                        "protocol_sha256",
                    )
                },
                "mode": run["mode"],
                "status": status,
                "checkpoint_id": cp,
                "trial_id": entry["trial_id"] if row else None,
                "source_trial_id": None if fresh else run["source"]["trial_id"],
                "incremental_api_cost_usd": ledger["cost_usd"],
                "known_incremental_api_cost_usd": ledger["known_api_cost_usd"],
                "prefix_api_cost_usd": 0 if fresh else prefix[1]["cost_usd"],
                "unknown_operations": ledger["unknown_operations"],
                "verifier_status": result.get("verification", {}).get("status"),
                "verifier_reward": int(result["rewards"]["reward"])
                if status in {"solved", "unsolved"}
                else None,
                "runtime_reason": result.get("reason"),
                "queue_seconds": metrics.get(entry["trial_id"], {}).get("queue_seconds"),
                "elapsed_seconds": metrics.get(entry["trial_id"], {}).get("elapsed_seconds"),
                "run_sha256": run["run_sha256"],
                **(
                    {"prefix_boundary": source_checkpoint["context"]["result"]["prefix_boundary"]}
                    if not fresh
                    and source_checkpoint
                    and "prefix_boundary" in source_checkpoint["context"]["result"]
                    else {}
                ),
            }
        )
    records_path = output / "records.jsonl"
    records_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))
    report = analyze(protocol, records, "pilot", mode=run["mode"])
    export(report, output / "analysis", plan=protocol, records=records_path)
    provenance = {
        "run_sha256": run["run_sha256"],
        "runtime_sha256": hashes,
        "records_sha256": hashlib.sha256(records_path.read_bytes()).hexdigest(),
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return report
