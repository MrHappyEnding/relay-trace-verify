"""Synthetic statistical acceptance on real task IDs; never calls agents or tests."""

import argparse
import hashlib
import json
from pathlib import Path

from rtv.benchmark.analysis import analyze
from rtv.benchmark.protocol import DEFAULT_PLAN, load, matrix, reservation_plan
from rtv.benchmark.report import export
from rtv.benchmark.source import audit, catalog_for


def main(args):
    plan = load(args.plan)
    args.output.mkdir(parents=True, exist_ok=False)
    ids = [t["instance_id"] for t in plan["tasks"] if t["split"] == "pilot"]
    rows = []
    for spec in matrix(plan, "pilot"):
        index = ids.index(spec["instance_id"])
        branch = spec["branch"]
        solved = index < (3 if branch == "B-summary" else 2)
        status = "solved" if solved else "unsolved"
        cp = "scripted-" + spec["pair_id"] if branch != "B-fresh" else None
        prefix = 0 if branch == "B-fresh" else 0.1
        cost = 0.2
        if index == 0 and branch == "B-workspace":
            status, cost = "infra_error", None
        if index == 3:
            status = "cancelled" if branch == "B-fresh" else "source_failed"
            cp, cost = None, 0
            prefix = 0 if branch == "B-fresh" else None
        rows.append(
            {
                **spec,
                "mode": "scripted",
                "status": status,
                "checkpoint_id": cp,
                "prefix_api_cost_usd": prefix,
                "incremental_api_cost_usd": cost,
                "unknown_operations": int(cost is None),
                "verifier_status": "completed" if status in {"solved", "unsolved"} else "not_run",
                "verifier_reward": int(solved) if status in {"solved", "unsolved"} else None,
            }
        )
    raw = args.output / "records.jsonl"
    raw.write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = analyze(plan, rows, "pilot", mode="scripted")
    export(report, args.output / "complete", plan=plan, records=raw)
    incomplete = [
        r for r in rows if not (r["instance_id"] == ids[1] and r["branch"] == "B-summary")
    ]
    partial_raw = args.output / "incomplete.jsonl"
    partial_raw.write_text("".join(json.dumps(r) + "\n" for r in incomplete))
    partial = analyze(plan, incomplete, "pilot", mode="scripted")
    export(partial, args.output / "incomplete", plan=plan, records=partial_raw)
    checks = {
        "outcome_blind_selection": audit(plan, catalog_for(args.plan))["selected_tasks"] == 24,
        "planned_pilot_branches": report["planned"] == report["received"] == 16,
        "planned_evaluation_branches": len(matrix(plan, "evaluation")) == 160,
        "paired_primary_difference": report["comparisons"]["B-summary minus A-continue"][
            "difference"
        ]
        == 0.25,
        "task_cluster_count": report["comparisons"]["B-summary minus A-continue"]["tasks"] == 4,
        "infra_visible": report["arms"]["B-workspace"]["outcomes"]["infra_error"] == 1,
        "source_failure_visible": report["arms"]["A-continue"]["outcomes"]["source_failed"] == 1,
        "cancel_visible": report["arms"]["B-fresh"]["outcomes"]["cancelled"] == 1,
        "unknown_cost_visible": report["arms"]["B-workspace"]["unknown_operations"] == 1,
        "missing_keeps_denominator": partial["missing"] == 1 and partial["planned"] == 16,
        "missing_withholds_ci": partial["comparisons"]["B-summary minus A-continue"]["ci95"]
        is None,
        "raw_input_preserved": (args.output / "complete/records.jsonl").read_bytes()
        == raw.read_bytes(),
        "paid_gate_closed": reservation_plan(plan, "evaluation")["paid_execution_enabled"] is False,
    }
    result = {
        "mode": "scripted-statistics-only",
        "note": "All outcomes, checkpoint IDs and costs are synthetic; no efficacy evidence.",
        "paid_api_calls": 0,
        "cloud_spend_usd": 0,
        "protocol_sha256": plan["protocol_sha256"],
        "checks": checks,
        "passed": all(checks.values()),
        "artifacts_sha256": {
            p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(args.output.rglob("*"))
            if p.is_file()
        },
    }
    (args.output / "acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(main(parser.parse_args()))
