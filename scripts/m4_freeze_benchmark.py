"""Rebuild the protocol from frozen public metadata and an inspected image map."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from rtv.benchmark.protocol import BRANCHES, SALT, seal, selection

HF_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
PARQUET_SHA256 = "a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd"
HARBOR_REVISION = "86723674f04e4209ac479d0fb75d9d9f44b4377e"


def main(args):
    import pyarrow.parquet as pq
    from harbor.models.task.task import Task

    if hashlib.sha256(args.parquet.read_bytes()).hexdigest() != PARQUET_SHA256:
        raise ValueError("Parquet differs from the pinned upstream snapshot")
    head = subprocess.check_output(
        ["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    if head != HARBOR_REVISION:
        raise ValueError("Upstream checkout is not the pinned Harbor revision")
    records = pq.read_table(
        args.parquet, columns=["instance_id", "repo", "base_commit"]
    ).to_pylist()
    if len(records) != 500:
        raise ValueError("Expected the complete 500-instance Verified snapshot")
    catalog = {
        "dataset": "princeton-nlp/SWE-bench_Verified",
        "revision": HF_REVISION,
        "parquet_sha256": hashlib.sha256(args.parquet.read_bytes()).hexdigest(),
        "records": sorted(records, key=lambda r: r["instance_id"]),
    }
    splits = selection(records)
    images = json.loads(args.images.read_text())
    tasks = []
    for split, rows in splits.items():
        for row in rows:
            relative = "datasets/swebench-verified/" + row["instance_id"]
            directory = args.upstream / relative
            task = Task(directory.resolve())
            config = json.loads((directory / "tests/config.json").read_text())
            if (
                config["instance_id"] != row["instance_id"]
                or config["base_commit"] != row["base_commit"]
            ):
                raise ValueError("Harbor adaptation and upstream dataset disagree")
            files = {}
            for path in sorted(directory.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Unexpected source symlink")
                if path.is_file():
                    files[path.relative_to(directory).as_posix()] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
            tasks.append(
                {
                    **row,
                    "split": split,
                    "harbor_path": relative,
                    "harbor_commit": HARBOR_REVISION,
                    "files_sha256": files,
                    "image": images[row["instance_id"]],
                    "resources": {
                        "cpus": task.config.environment.cpus,
                        "memory_mb": task.config.environment.memory_mb,
                        "storage_mb": task.config.environment.storage_mb,
                    },
                }
            )
    body = {
        "schema_version": 1,
        "name": "rtv-swebench-verified-v1",
        "status": "selection-and-statistics-frozen; paid runtime integration pending",
        "sources": {
            "dataset": catalog["dataset"],
            "dataset_revision": HF_REVISION,
            "parquet_sha256": catalog["parquet_sha256"],
            "harbor_git_url": "https://github.com/laude-institute/harbor-datasets.git",
            "harbor_commit": HARBOR_REVISION,
            "harbor_registry_version": "1.0",
            "harbor_runtime": "0.24.0",
        },
        "selection": {
            "method": "sha256-rank-before-outcomes",
            "salt": SALT,
            "catalog_count": 500,
            "pilot_count": 4,
            "evaluation_count": 20,
            "exclusions": "none; unsupported environments remain recorded, no substitution",
        },
        "tasks": tasks,
        "branches": list(BRANCHES),
        "repeats": {"pilot": 1, "evaluation": 2},
        "agents": {
            "A": {
                "scaffold": "codex-cli",
                "version": "0.160.1",
                "model": "gpt-6-astra",
                "billing": "subscription; usage reported, no invented USD price",
            },
            "B": {
                "scaffold": "mini-swe-agent",
                "version": "2.4.6",
                "model": "gpt-5.4-mini-2026-03-17",
                "billing": "metered API",
            },
        },
        "budget": {
            "primary_constraint": "wall-clock service time, queue reported separately",
            "prefix_seconds": 300,
            "continuation_seconds": 300,
            "fresh_seconds": 600,
            "api_requests": {"A-continue": 0, "B-workspace": 8, "B-summary": 8, "B-fresh": 16},
            "reservation_per_request_usd": 0.062,
            "reservation_status": "M3 transport envelope; revalidate pricing before paid approval",
            "automatic_retries": 0,
            "paid_execution_authorized": False,
        },
        "checkpoint": {
            "trigger": "one naturally completed source turn within prefix limit",
            "on_prefix_timeout": "no checkpoint, source_failed for continuation arms; keep costs",
            "eligibility": "all planned tasks; never inspect hidden reward "
            "to decide whether to fork",
            "hidden_verification": "final branches only; no hidden feedback in handoff",
            "summary": "source final message, labeled unverified; no extra model call",
        },
        "statistics": {
            "primary_contrast": "B-summary minus A-continue",
            "unit": "task; keep repeats and branches within their task cluster",
            "bootstrap_replicates": 10000,
            "bootstrap_seed": 20261011,
            "ci": "paired task-cluster percentile 95%; no CI with missing observations",
            "secondary": "descriptive exploratory contrasts; no unadjusted significance claims",
            "infra_policy": "unsuccessful in ITT; graded-only denominator separately visible",
            "unknown_cost": "null plus known lower bound; never zero-fill",
            "reruns": "no automatic replacement; new protocol/run identity "
            "and retain prior failures",
            "scope": "20-task fixed exploratory subset; not an official 500-task score",
        },
        "execution_gate": [
            "real-repository checkpoint/restore compatibility",
            "offline isolated verifier negative and oracle controls",
            "Kubernetes memory/storage quota and AMD64 capability",
            "credential delivery and task-aware agent prompts",
            "current price/envelope check and explicit paid budget approval",
        ],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    catalog_path = args.output / "swebench-verified-catalog.json"
    if catalog_path.exists() and json.loads(catalog_path.read_text()) != catalog:
        raise ValueError("Existing catalog differs; choose a new release directory")
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    plan = seal(body)
    plan_path = args.output / "swebench-verified-v1.json"
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise ValueError("Frozen protocol differs; choose a new release directory")
    plan_path.write_text(json.dumps(plan, indent=2) + "\n")
    print(
        json.dumps(
            {"protocol_sha256": plan["protocol_sha256"], "selected": len(tasks), "paid_calls": 0}
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--parquet", required=True, type=Path)
    p.add_argument("--upstream", required=True, type=Path)
    p.add_argument("--images", required=True, type=Path)
    p.add_argument("--output", type=Path, default=Path("configs/benchmarks"))
    main(p.parse_args())
