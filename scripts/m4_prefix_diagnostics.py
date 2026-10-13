"""Grade frozen prefixes only after an entire declared round has finished.

This is offline, post hoc diagnosis. It does not submit trials, use credentials,
filter observations, change budgets, or feed hidden test results to any agent.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from rtv.benchmark.execution import validate_run
from rtv.benchmark.protocol import digest, load
from rtv.harbor_probe import tree_digest
from rtv.runtime import checkpoint, tasks
from rtv.runtime.config import Settings
from rtv.runtime.verification import isolated_verify


def inputs(roots):
    """Validate the whole round before starting any diagnostic container."""
    if len(set(roots)) != len(roots):
        raise ValueError("Duplicate runtime root")
    runs = []
    for root in roots:
        report = json.loads((root / "report.json").read_text())
        prepared = json.loads((root / "prepared.json").read_text())
        body = {k: v for k, v in prepared.items() if k != "prepared_sha256"}
        if digest(body) != prepared["prepared_sha256"]:
            raise ValueError("Prepared run changed")
        run = prepared["run"]
        validate_run(load(Path(prepared["protocol"])), run)
        if (
            Path(run["root"]).resolve() != root
            or report["run_sha256"] != run["run_sha256"]
            or report["mode"] != "real-model-pilot"
            or report["status"] not in {"passed", "failed"}
            or not report["checks"].get("four_observations_linked")
            or not report["checks"].get("all_allocations_released")
        ):
            raise ValueError("Round must finish collection and cleanup before diagnosis")
        runs.append((root, run))
    return runs


async def diagnose(roots, output):
    from harbor.models.task.task import Task

    runs = inputs(roots)
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for root, run in runs:
        source = run["source"]
        task_id = source["spec"]["task_contract"]["instance_id"]
        directory = output / source["trial_id"]
        directory.mkdir()
        cp_path = root / "observations/source-checkpoint.json"
        result = {
            "task": task_id,
            "run_sha256": run["run_sha256"],
            "model_calls": 0,
            "timing": "after collection and cleanup of every supplied run; no agent feedback",
        }
        started = time.monotonic()
        try:
            if not cp_path.is_file():
                result.update(status="not_gradeable", reason="no source checkpoint")
            else:
                cp = json.loads(cp_path.read_text())
                if (
                    cp["trial_id"] != source["trial_id"]
                    or cp["state"] != "committed"
                    or cp["context"]["result"].get("status") != "sealed"
                ):
                    raise ValueError("Expected the committed sealed source checkpoint")
                restored = checkpoint.validate(Settings(root, "", ""), cp, directory / "restored")
                spec = source["spec"]
                task_dir = tasks.directory(spec)
                if tree_digest(task_dir) != spec["task_digest"]:
                    raise ValueError("Task definition changed")
                workspace = restored / "workspace"
                before = tasks.workspace_digest(workspace, spec)
                row = {"spec": spec, "execution_id": "posthoc-" + uuid4().hex}
                rewards, verification = await isolated_verify(
                    Task(task_dir), row, workspace, directory
                )
                if tasks.workspace_digest(workspace, spec) != before:
                    raise ValueError("Diagnostic changed its source workspace")
                result.update(
                    status="completed",
                    reward=rewards["reward"],
                    checkpoint_id=cp["checkpoint_id"],
                    checkpoint_manifest_sha256=cp["manifest_sha256"],
                    verification=verification,
                    source_unchanged=True,
                )
        except Exception as exc:
            result.update(status="failed", error=type(exc).__name__)
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (directory / "diagnostic.json").write_text(json.dumps(result, indent=2) + "\n")
        results.append(result)
        print(json.dumps(result), flush=True)
    (output / "report.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(any(r["status"] == "failed" for r in results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(diagnose([p.resolve() for p in args.roots], args.output.resolve()))
    )
