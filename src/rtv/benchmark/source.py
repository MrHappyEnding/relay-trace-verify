"""Pinned upstream checkout and content audit. Never use source solution as agent context."""

import hashlib
import json
import subprocess
from pathlib import Path

from rtv.benchmark.protocol import selection, validate


def audit(plan, catalog, upstream=None):
    validate(plan)
    if (
        catalog["revision"] != plan["sources"]["dataset_revision"]
        or catalog["parquet_sha256"] != plan["sources"]["parquet_sha256"]
        or len(catalog["records"]) != plan["selection"]["catalog_count"]
    ):
        raise ValueError("Dataset catalog provenance mismatch")
    splits = selection(
        catalog["records"], plan["selection"]["pilot_count"], plan["selection"]["evaluation_count"]
    )
    expected = {(r["instance_id"], split): r for split, rows in splits.items() for r in rows}
    if set(expected) != {(r["instance_id"], r["split"]) for r in plan["tasks"]}:
        raise ValueError("Frozen tasks do not follow the outcome-blind selection")
    files = 0
    for task in plan["tasks"]:
        source = expected[(task["instance_id"], task["split"])]
        if any(task[k] != source[k] for k in ("repo", "base_commit")):
            raise ValueError("Task base identity mismatch")
        if upstream is not None:
            directory = Path(upstream) / task["harbor_path"]
            actual = {}
            for path in directory.rglob("*"):
                if path.is_symlink():
                    raise ValueError("Source symlink rejected")
                if path.is_file():
                    actual[path.relative_to(directory).as_posix()] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
            if actual != task["files_sha256"]:
                raise ValueError("Upstream files changed: " + task["instance_id"])
            files += len(actual)
    return {
        "protocol_sha256": plan["protocol_sha256"],
        "catalog_count": len(catalog["records"]),
        "selected_tasks": len(plan["tasks"]),
        "verified_files": files,
        "paid_api_calls": 0,
        "source_audited": upstream is not None,
    }


def materialize(plan, destination):
    validate(plan)
    url = plan["sources"]["harbor_git_url"]
    revision = plan["sources"]["harbor_commit"]
    if url != "https://github.com/laude-institute/harbor-datasets.git":
        raise ValueError("This importer only supports the frozen official Harbor dataset source")
    if destination.exists():
        current = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
        ).strip()
        if current != revision:
            raise ValueError("Existing checkout has a different revision; use a new directory")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(destination)], check=True, capture_output=True)
        base = ["git", "-C", str(destination)]
        subprocess.run([*base, "remote", "add", "origin", url], check=True)
        subprocess.run(
            [*base, "fetch", "--depth=1", "--filter=blob:none", "origin", revision], check=True
        )
        subprocess.run([*base, "sparse-checkout", "init", "--cone"], check=True)
        subprocess.run(
            [*base, "sparse-checkout", "set", *[t["harbor_path"] for t in plan["tasks"]]],
            check=True,
        )
        subprocess.run([*base, "checkout", "--detach", revision], check=True)
    return {"path": str(destination.resolve()), "revision": revision, "paid_api_calls": 0}


def catalog_for(path):
    return json.loads(Path(path).with_name("swebench-verified-catalog.json").read_text())
