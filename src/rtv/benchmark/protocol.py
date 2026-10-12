"""Outcome-blind selection, immutable protocols and stable planned observations."""

import hashlib
import json
import re
from pathlib import Path

SALT = "rtv-m4-swe-v1"
BRANCHES = ("A-continue", "B-workspace", "B-summary", "B-fresh")
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PLAN = ROOT / "configs/benchmarks/swebench-verified-v1.json"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def selection(records, pilot=4, evaluation=20):
    if type(pilot) is not int or type(evaluation) is not int or min(pilot, evaluation) < 1:
        raise ValueError("Positive split sizes required")
    if len(records) < pilot + evaluation:
        raise ValueError("Insufficient catalog records")
    ids = [r["instance_id"] for r in records]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate task identity")
    for row in records:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-[0-9]+", row["instance_id"]):
            raise ValueError("Invalid task identity")
        if not re.fullmatch("[a-f0-9]{40}", row["base_commit"]):
            raise ValueError("Unpinned task base commit")
    ordered = sorted(
        records, key=lambda r: hashlib.sha256((SALT + ":" + r["instance_id"]).encode()).hexdigest()
    )
    return {"pilot": ordered[:pilot], "evaluation": ordered[pilot : pilot + evaluation]}


def seal(body):
    if "protocol_sha256" in body:
        raise ValueError("Protocol already sealed")
    return {**body, "protocol_sha256": digest(body)}


def validate(plan):
    body = {k: v for k, v in plan.items() if k != "protocol_sha256"}
    if plan.get("schema_version") != 1 or plan.get("protocol_sha256") != digest(body):
        raise ValueError("Protocol identity or contents changed")
    if plan["branches"] != list(BRANCHES):
        raise ValueError("Unsupported comparison arms")
    if (
        plan["selection"]["method"] != "sha256-rank-before-outcomes"
        or plan["selection"]["salt"] != SALT
    ):
        raise ValueError("Unsupported selection rule")
    ids = [t["instance_id"] for t in plan["tasks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Split leakage or duplicate task")
    for task in plan["tasks"]:
        if task["split"] not in {"pilot", "evaluation"}:
            raise ValueError("Invalid split")
        if not re.fullmatch(r"[a-f0-9]{40}", task["harbor_commit"]):
            raise ValueError("Harbor source must be pinned")
        if task["harbor_path"] != "datasets/swebench-verified/" + task["instance_id"]:
            raise ValueError("Unexpected upstream task path")
    for task in plan["tasks"]:
        if (
            task["harbor_commit"] != plan["sources"]["harbor_commit"]
            or not re.fullmatch("[a-f0-9]{40}", task["base_commit"])
            or task["image"]["platform"] != "linux/amd64"
            or not re.fullmatch(r"[a-zA-Z0-9/._-]+@sha256:[a-f0-9]{64}", task["image"]["pinned"])
        ):
            raise ValueError("Unpinned source or environment identity")
        for name, checksum in task["files_sha256"].items():
            path = Path(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not re.fullmatch("[a-f0-9]{64}", checksum)
            ):
                raise ValueError("Invalid artifact manifest")
    for split in ("pilot", "evaluation"):
        if sum(t["split"] == split for t in plan["tasks"]) != plan["selection"][split + "_count"]:
            raise ValueError("Split size does not match the frozen selection")
    for split in ("pilot", "evaluation"):
        count = plan["repeats"][split]
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError("Invalid repeat count")
    if "prefix_policy" in plan["checkpoint"]:
        from rtv.codex_gate import policy

        policy(plan["checkpoint"]["prefix_policy"])
        baseline = load(DEFAULT_PLAN)
        if plan.get("environment_protocol_sha256") != baseline["protocol_sha256"] or any(
            plan[k] != baseline[k] for k in ("sources", "selection", "tasks")
        ):
            raise ValueError("Tool-prefix protocol changed its preflight environment lineage")
    elif "environment_protocol_sha256" in plan:
        raise ValueError("Environment lineage override requires a versioned tool-prefix policy")
    return plan


def environment_protocol(plan):
    """A new execution policy may reuse an identical audited task/environment catalog."""
    validate(plan)
    return plan.get("environment_protocol_sha256", plan["protocol_sha256"])


def load(path=DEFAULT_PLAN):
    return validate(json.loads(Path(path).read_text()))


def matrix(plan, split):
    validate(plan)
    if split not in {"pilot", "evaluation"}:
        raise ValueError("Select pilot or evaluation explicitly")
    result = []
    for task in plan["tasks"]:
        if task["split"] != split:
            continue
        for repeat in range(plan["repeats"][split]):
            pair = digest([plan["protocol_sha256"], task["instance_id"], repeat])[:24]
            # Predefined within-block order; never order by reward or runtime observations.
            order = sorted(BRANCHES, key=lambda label: digest([pair, label]))
            for index, branch in enumerate(order):
                result.append(
                    {
                        "observation_id": digest([pair, branch])[:24],
                        "pair_id": pair,
                        "instance_id": task["instance_id"],
                        "repeat": repeat,
                        "split": split,
                        "branch": branch,
                        "order": index,
                        "protocol_sha256": plan["protocol_sha256"],
                    }
                )
    return result


def reservation_plan(plan, split):
    """Admission envelopes, not an invoice estimate or permission to call models."""
    schedule = matrix(plan, split)
    pairs = len({r["pair_id"] for r in schedule})
    budgets = plan["budget"]
    requests = sum(budgets["api_requests"][r["branch"]] for r in schedule)
    return {
        "split": split,
        "task_count": len({r["instance_id"] for r in schedule}),
        "paired_repeats": pairs,
        "branch_runs": len(schedule),
        "prefix_runs": pairs,
        "codex_subscription_stages": 2 * pairs,
        "max_api_requests": requests,
        "api_reservation_usd": round(requests * budgets["reservation_per_request_usd"], 6),
        "paid_execution_enabled": False,
        "note": "Planning reservations only; pricing and actual transport must be validated, "
        "then the owner must approve the concrete run.",
    }
