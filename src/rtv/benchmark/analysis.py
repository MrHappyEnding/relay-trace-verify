"""Task-cluster paired bootstrap; missing rows and infrastructure failures stay visible."""

import math
import random
from collections import Counter, defaultdict

from rtv.benchmark.protocol import BRANCHES, matrix

STATES = {"solved", "unsolved", "infra_error", "source_failed", "cancelled", "missing"}
MODES = {"real-model", "scripted", "oracle"}


def percentile(values, p):
    values = sorted(values)
    x = (len(values) - 1) * p
    low = int(x)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (x - low)


def cluster_difference(values, *, samples=10000, seed=20261011):
    """Input contains one mean paired difference per task, not per trial."""
    if type(samples) is not int or not 100 <= samples <= 100000:
        raise ValueError("Bootstrap sample count outside supported bounds")
    if not values:
        return {"tasks": 0, "difference": None, "ci95": None}
    point = sum(values) / len(values)
    if len(values) < 2:
        return {"tasks": len(values), "difference": point, "ci95": None}
    rng = random.Random(seed)
    draws = [sum(rng.choices(values, k=len(values))) / len(values) for _ in range(samples)]
    return {
        "tasks": len(values),
        "difference": point,
        "ci95": [percentile(draws, 0.025), percentile(draws, 0.975)],
    }


def analyze(plan, supplied, split, *, mode, bootstrap_samples=None):
    if mode not in MODES:
        raise ValueError("Explicit homogeneous evidence mode required")
    planned = matrix(plan, split)
    if bootstrap_samples is None:
        bootstrap_samples = plan["statistics"]["bootstrap_replicates"]
    expected = {r["observation_id"]: r for r in planned}
    observed = {}
    sources = {}
    prefix_costs = {}
    checkpoint_pairs = {}
    failed_sources = set()
    for row in supplied:
        key = row.get("observation_id")
        if key not in expected or key in observed:
            raise ValueError(
                "Unexpected or duplicate observation; do not silently overwrite reruns"
            )
        spec = expected[key]
        if any(
            row.get(k) != spec[k] for k in ("protocol_sha256", "instance_id", "repeat", "branch")
        ):
            raise ValueError("Observation does not match its frozen assignment")
        if any(k in row and row[k] != v for k, v in spec.items()):
            raise ValueError("Observation cannot override planned pairing or ordering")
        if row.get("mode") != mode or row.get("status") not in STATES - {"missing"}:
            raise ValueError("Mixed evidence modes or unsupported outcome")
        for name in ("incremental_api_cost_usd", "prefix_api_cost_usd"):
            if name not in row:
                raise ValueError("Explicit cost fields required; use null for UNKNOWN")
            v = row.get(name)
            if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0):
                raise ValueError("Cost must be finite, nonnegative, or null for UNKNOWN")
        if "known_incremental_api_cost_usd" in row:
            lower = row["known_incremental_api_cost_usd"]
            total = row["incremental_api_cost_usd"]
            if (
                type(lower) not in (int, float)
                or not math.isfinite(lower)
                or lower < 0
                or (total is not None and lower > total + 1e-9)
            ):
                raise ValueError("Invalid known cost lower bound")
        if type(row.get("unknown_operations")) is not int or row["unknown_operations"] < 0:
            raise ValueError("UNKNOWN operation count required")
        if row["unknown_operations"] and row.get("incremental_api_cost_usd") is not None:
            raise ValueError("An uncertain operation cannot be presented as fully known cost")
        cp = row.get("checkpoint_id")
        if cp is not None and (not isinstance(cp, str) or not cp.strip()):
            raise ValueError("Checkpoint identity must be a nonempty string or null")
        if row["status"] == "source_failed":
            if cp is not None or spec["branch"] == "B-fresh":
                raise ValueError("Source failure cannot have a checkpoint or apply to a fresh run")
            failed_sources.add(spec["pair_id"])
        if spec["branch"] == "B-fresh":
            if cp is not None or row.get("prefix_api_cost_usd") != 0:
                raise ValueError(
                    "Fresh baseline must start from the original task without prefix cost"
                )
        else:
            pair = spec["pair_id"]
            prefix = row["prefix_api_cost_usd"]
            if pair in prefix_costs and prefix_costs[pair] != prefix:
                raise ValueError("Paired branches disagree about prefix accounting")
            prefix_costs[pair] = prefix
            if row["status"] not in {"source_failed", "cancelled", "infra_error"} and not cp:
                raise ValueError("Continuation outcome requires its source checkpoint")
        if cp:
            pair = spec["pair_id"]
            if cp in checkpoint_pairs and checkpoint_pairs[cp] != pair:
                raise ValueError("Different task repeats cannot reuse the same source checkpoint")
            checkpoint_pairs[cp] = pair
            source = (cp, row.get("prefix_api_cost_usd"))
            if pair in sources and sources[pair] != source:
                raise ValueError("Paired branches disagree about checkpoint or prefix accounting")
            sources[pair] = source
        if row["status"] in {"solved", "unsolved"} and row.get("verifier_status") != "completed":
            raise ValueError("Graded outcomes require a completed verifier")
        if row["status"] in {"solved", "unsolved"} and (
            type(row.get("verifier_reward")) is not int
            or row["verifier_reward"] != int(row["status"] == "solved")
        ):
            raise ValueError("Graded outcome disagrees with binary verifier reward")
        observed[key] = row
    if failed_sources & sources.keys():
        raise ValueError("Paired branches disagree about source failure")
    rows = []
    for spec in planned:
        row = observed.get(
            spec["observation_id"],
            {
                "status": "missing",
                "mode": mode,
                "incremental_api_cost_usd": None,
                "prefix_api_cost_usd": None,
                "unknown_operations": 0,
            },
        )
        rows.append({**spec, **row})
    arms = {}
    for branch in BRANCHES:
        arm = [r for r in rows if r["branch"] == branch]
        counts = Counter(r["status"] for r in arm)
        valid = counts["solved"] + counts["unsolved"]
        known_cost = []
        for r in arm:
            value = r.get("incremental_api_cost_usd")
            if value is None:
                value = r.get("known_incremental_api_cost_usd")
            if value is not None:
                known_cost.append(value)
        known_prefix_cost = [
            r["prefix_api_cost_usd"] for r in arm if r.get("prefix_api_cost_usd") is not None
        ]
        arms[branch] = {
            "planned": len(arm),
            "outcomes": dict(counts),
            "graded": valid,
            "resolved_itt": counts["solved"] / len(arm) if arm else None,
            "resolved_among_graded": counts["solved"] / valid if valid else None,
            "known_incremental_api_cost_usd": sum(known_cost),
            "known_end_to_end_api_cost_usd": sum(known_cost) + sum(known_prefix_cost),
            "unknown_end_to_end_cost_rows": sum(
                r.get("incremental_api_cost_usd") is None or r.get("prefix_api_cost_usd") is None
                for r in arm
            ),
            "unknown_cost_rows": sum(r.get("incremental_api_cost_usd") is None for r in arm),
            "unknown_operations": sum(r["unknown_operations"] for r in arm),
        }
    by_pair = defaultdict(dict)
    for row in rows:
        by_pair[(row["instance_id"], row["repeat"])][row["branch"]] = row
    comparisons = {}
    for left, right in [
        ("B-summary", "A-continue"),
        ("B-workspace", "A-continue"),
        ("B-summary", "B-workspace"),
        ("B-fresh", "A-continue"),
    ]:
        tasks = defaultdict(list)
        paired_graded = 0
        for (task, _), pair in by_pair.items():
            a, b = pair[left], pair[right]
            tasks[task].append(int(a["status"] == "solved") - int(b["status"] == "solved"))
            paired_graded += a["status"] in {"solved", "unsolved"} and b["status"] in {
                "solved",
                "unsolved",
            }
        values = [sum(v) / len(v) for v in tasks.values()]
        comparison_missing = any(
            r["branch"] in {left, right} and r["status"] == "missing" for r in rows
        )
        comparisons[f"{left} minus {right}"] = {
            **cluster_difference(
                values, samples=bootstrap_samples, seed=plan["statistics"]["bootstrap_seed"]
            ),
            "complete_graded_pairs": paired_graded,
            "planned_pairs": len(by_pair),
            "primary": (left, right) == ("B-summary", "A-continue"),
            "scope": "end-to-end baseline" if left == "B-fresh" else "checkpoint continuation",
            "provisional": comparison_missing,
        }
        if comparison_missing:
            comparisons[f"{left} minus {right}"]["ci95"] = None
        if not any(r["branch"] in {left, right} and r["status"] != "missing" for r in rows):
            comparisons[f"{left} minus {right}"]["difference"] = None
    return {
        "schema_version": 1,
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_seed": plan["statistics"]["bootstrap_seed"],
        "protocol_sha256": plan["protocol_sha256"],
        "split": split,
        "mode": mode,
        "observations": rows,
        "arms": arms,
        "comparisons": comparisons,
        "received": len(supplied),
        "planned": len(planned),
        "missing": sum(r["status"] == "missing" for r in rows),
        "inference": "No model-effect claim: synthetic/oracle data"
        if mode != "real-model"
        else "Exploratory fixed subset; task-cluster CI, no population superiority claim",
        "cost_note": "Known API spend is a lower bound when any cost is UNKNOWN. "
        "Subscription stages are not priced as free inference. Prefix cost is "
        "included per continuation strategy, not summed over physical forks.",
    }
