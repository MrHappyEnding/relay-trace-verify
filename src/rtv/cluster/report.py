"""Derived reliability records; incomplete trials and unknown usage stay visible."""

import csv
import hashlib
import json
from datetime import UTC, datetime

from rtv.runtime.config import TERMINAL


def quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def trial_metrics(row, events):
    starts = [e["at"] for e in events if e["kind"] == "execution.claimed"]
    latest = row["execution_id"]
    current_finish = [
        e["at"]
        for e in events
        if e["kind"] == "trial.finished" and e["payload"]["execution_id"] == latest
    ]
    recoveries = [e for e in events if e["kind"] == "execution.recovered"]
    recovery_latencies = []
    for e in recoveries:
        after = [
            s["at"]
            for s in events
            if s["kind"] == "execution.claimed"
            and s["payload"]["execution_id"] == e["payload"]["execution_id"]
        ]
        if after:
            recovery_latencies.append((min(after) - e["at"]).total_seconds())
    return {
        "trial_id": row["trial_id"],
        "status": row["status"],
        "terminal": row["status"] in TERMINAL,
        "reason": (row["result"] or {}).get("reason"),
        "cleanup": row["cleanup"],
        "executions_started": len(starts),
        "recoveries": len(recoveries),
        "queue_seconds": (min(starts) - row["created_at"]).total_seconds() if starts else None,
        "elapsed_seconds": (max(current_finish) - row["created_at"]).total_seconds()
        if current_finish and row["status"] in TERMINAL
        else None,
        "recovery_to_claim_seconds": recovery_latencies,
    }


def export_report(settings, config, output):
    from rtv.cluster.store import ClusterStore

    if output.exists():
        raise ValueError("Report output already exists; choose a new immutable snapshot")
    store = ClusterStore(settings)
    store.verify_pool(config)
    with store.connect() as c:
        ids = c.execute(
            "SELECT trial_id FROM trials WHERE root=%s ORDER BY created_at", (str(settings.root),)
        ).fetchall()
    output.mkdir(parents=True)
    rows, hashes = [], {}
    for entry in ids:
        trial_id = entry["trial_id"]
        row = store.get(trial_id)
        events = store.events(trial_id)
        ledger = store.ledger(trial_id)
        metrics = trial_metrics(row, events)
        metrics.update(
            unknown_operations=ledger["unknown_operations"],
            known_api_cost_usd=ledger["known_api_cost_usd"],
        )
        rows.append(metrics)
        path = output / "trials" / (trial_id + ".json")
        path.parent.mkdir(exist_ok=True)
        data = {
            "spec": row["spec"],
            "result": row["result"],
            "events": events,
            "ledger": ledger,
            "executions": store.executions(trial_id),
        }
        path.write_text(json.dumps(data, indent=2, default=str) + "\n")
        hashes[str(path.relative_to(output))] = hashlib.sha256(path.read_bytes()).hexdigest()
    if rows:
        with (output / "summary.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    latencies = [n for row in rows for n in row["recovery_to_claim_seconds"]]
    result = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": "model-free local Kubernetes reliability; not agent effectiveness",
        "trials": rows,
        "pool": store.pool_status(),
        "config": config.frozen(),
        "sha256": hashes,
        "trial_count": len(rows),
        "terminal_count": sum(r["terminal"] for r in rows),
        "incomplete_count": sum(not r["terminal"] for r in rows),
        "recovery_to_claim_seconds": {
            "n": len(latencies),
            "p50": quantile(latencies, 0.5),
            "p95": quantile(latencies, 0.95),
        },
        "unknown_operations": sum(r["unknown_operations"] for r in rows),
        "known_api_cost_usd": sum(r["known_api_cost_usd"] for r in rows),
        "limitations": [
            "k3d nodes share one Docker Desktop host",
            "shared local bind storage, not HA",
            "recovery latency begins at detection/epoch transfer, not injected fault time",
            "no benchmark success-rate, cost-advantage or confidence-interval claim",
        ],
    }
    (output / "report.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result
