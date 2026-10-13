"""Serial frozen evaluation: environment controls, budget admission, collection and archival.

Run this coordinator again to continue unstarted pairs. An admitted pair without a
settlement blocks further spending until its runtime has been reconciled; it is never replayed.
"""

import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from rtv.benchmark.analysis import analyze
from rtv.benchmark.archive import archive_completed
from rtv.benchmark.batch import admit, exposure, settle
from rtv.benchmark.protocol import DEFAULT_PLAN, digest, load, matrix
from rtv.benchmark.report import export
from rtv.cluster.config import ClusterConfig
from rtv.cluster.repository import prepare as package
from rtv.mini_agent import api_envelope
from rtv.runtime.config import REPO
from rtv.runtime.tasks import prepare_contract


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def invoke(script, arguments, log):
    with log.open("a") as stream:
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / script), *map(str, arguments)],
            stdout=stream,
            stderr=stream,
        )
    return result.returncode


def fingerprint():
    paths = sorted(
        [
            *REPO.glob("src/**/*.py"),
            *REPO.glob("scripts/*.py"),
            *REPO.glob("infra/m4/**/*.py"),
            REPO / "infra/m4/Dockerfile",
            REPO / "infra/m4/Dockerfile.dockerignore",
            REPO / "uv.lock",
        ]
    )
    return digest(
        {
            str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
            if p.is_file()
        }
    )


def prepared_task(args, protocol, config, task):
    name = task["instance_id"]
    local = args.environments / name
    local.mkdir(parents=True, exist_ok=True)
    flight = local / "preflight/preflight.json"
    if not flight.exists():
        if flight.parent.exists():
            raise ValueError("Interrupted preflight needs explicit reconciliation")
        invoke(
            "m4_benchmark_preflight.py",
            [
                "--plan",
                args.protocol,
                "--split",
                "evaluation",
                "--upstream",
                args.upstream,
                "--task",
                name,
                "--output",
                flight.parent,
            ],
            local / "preflight.log",
        )
    if not flight.exists() or json.loads(flight.read_text())["status"] != "passed":
        raise ValueError("Environment preflight did not pass")
    contract = local / "base-contract.json"
    if not contract.exists():
        write(
            contract,
            prepare_contract(args.protocol, args.upstream, name, flight, split="evaluation"),
        )
    codex = local / "codex/prepare.json"
    if not codex.exists():
        if codex.parent.exists():
            raise ValueError("Interrupted image preparation needs explicit reconciliation")
        invoke(
            "m4_repository_codex_prepare.py",
            ["--contract", contract, "--output", codex.parent],
            local / "codex.log",
        )
    result = json.loads(codex.read_text())
    if result["status"] != "passed":
        raise ValueError("Agent image preparation did not pass")
    output = args.root / "packages" / name
    if not (output / "config.json").exists():
        declared = prepare_contract(
            args.protocol, args.upstream, name, flight, result["agent_image_id"], split="evaluation"
        )
        package(config, declared, output)
    return output


def summarize(args, protocol, state):
    records = []
    for task in state["tasks"].values():
        records.extend(task.get("environment_observations", []))
    for pair in state["pairs"].values():
        if pair["state"] != "settled":
            continue
        path = Path(pair["root"]) / "observations/records.jsonl"
        if path.exists():
            records.extend(
                json.loads(line) for line in path.read_text().splitlines() if line.strip()
            )
    report = analyze(protocol, records, "evaluation", mode="real-model")
    output = args.control / "reports" / digest(records)[:20]
    if not output.exists():
        raw = args.control / "records.jsonl"
        raw.write_text("".join(json.dumps(r) + "\n" for r in records))
        export(report, output, plan=protocol, records=raw)
    write(
        args.control / "progress.json",
        {
            "received": report["received"],
            "planned": report["planned"],
            "budget_exposure_usd": float(exposure(state)),
            "report": str(output),
            "settled_pairs": sum(p["state"] == "settled" for p in state["pairs"].values()),
            "environment_failures": sum(
                t.get("status") == "environment_failed" for t in state["tasks"].values()
            ),
        },
    )
    return report


def main(args):
    protocol = load(args.protocol)
    config = ClusterConfig(**json.loads(args.config.read_text()))
    if config.model_access or config.max_jobs != 1:
        raise ValueError("Use an uncredentialed serial executor pool")
    args.root = args.root.resolve()
    args.control = args.control.resolve()
    args.environments = args.environments.resolve()
    args.control.mkdir(parents=True, exist_ok=True)
    lock = (args.control / "coordinator.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    envelope = api_envelope({"api_envelope": "mini-160kb-v1"})
    reservation = round(32 * envelope.reservation_usd, 9)
    schedule = matrix(protocol, "evaluation")
    manifest = {
        "protocol_sha256": protocol["protocol_sha256"],
        "schedule": schedule,
        "root": str(args.root),
        "runner_image": config.runner_image,
        "source_sha256": fingerprint(),
        "approved_total_usd": args.approved_total_usd,
        "prior_cost_usd": args.prior_cost_usd,
        "pair_reservation_usd": reservation,
        "api_envelope": "mini-160kb-v1",
        "automatic_paid_retries": 0,
    }
    frozen = args.control / "manifest.json"
    if frozen.exists():
        if json.loads(frozen.read_text()) != manifest:
            raise ValueError("Frozen batch identity changed")
    else:
        write(frozen, manifest)
    path = args.control / "state.json"
    state = (
        json.loads(path.read_text())
        if path.exists()
        else {
            "prior_cost_usd": args.prior_cost_usd,
            "approved_total_usd": args.approved_total_usd,
            "manifest_sha256": digest(manifest),
            "pairs": {},
            "tasks": {},
        }
    )
    if state["manifest_sha256"] != digest(manifest):
        raise ValueError("Batch journal does not match its manifest")
    selected = [t for t in protocol["tasks"] if t["split"] == "evaluation"]
    if args.stop_after_task:
        names = [t["instance_id"] for t in selected]
        if args.stop_after_task not in names:
            raise ValueError("Stop boundary is not a frozen evaluation task")
        selected = selected[: names.index(args.stop_after_task) + 1]
    write(
        args.control / "execution-boundary.json",
        {
            "stop_after_task": args.stop_after_task,
            "selected_task_count": len(selected),
            "full_schedule_unchanged": True,
        },
    )
    for task in selected:
        name = task["instance_id"]
        task_state = state["tasks"].setdefault(name, {})
        if task_state.get("status") in {"completed", "environment_failed"}:
            continue
        if shutil.disk_usage(args.root.parent).free < 10 * 1024**3:
            raise ValueError("Less than 10 GiB free; archive completed artifacts before continuing")
        print(
            json.dumps(
                {"task": name, "phase": "environment", "exposure_usd": float(exposure(state))}
            ),
            flush=True,
        )
        try:
            delivered = prepared_task(args, protocol, config, task)
        except Exception as exc:
            # Environment failures retain every assigned observation without API calls.
            task_state.update(
                status="environment_failed",
                error_type=type(exc).__name__,
                environment_observations=[
                    {
                        **r,
                        "mode": "real-model",
                        "status": "infra_error",
                        "checkpoint_id": None,
                        "incremental_api_cost_usd": 0,
                        "prefix_api_cost_usd": 0,
                        "unknown_operations": 0,
                        "reason": "environment_preparation_failed",
                        "verifier_status": "not_run",
                    }
                    for r in schedule
                    if r["instance_id"] == name
                ],
            )
            write(path, state)
            summarize(args, protocol, state)
            print(
                json.dumps(
                    {"task": name, "phase": "environment_failed", "error_type": type(exc).__name__}
                ),
                flush=True,
            )
            continue
        for repeat in range(protocol["repeats"]["evaluation"]):
            key = next(
                r["pair_id"] for r in schedule if r["instance_id"] == name and r["repeat"] == repeat
            )
            root = args.root / "runs" / name / str(repeat)
            plan = args.control / "pairs" / (key + ".json")
            plan.parent.mkdir(exist_ok=True)
            if key in state["pairs"]:
                if state["pairs"][key]["state"] == "started":
                    if not (root / "report.json").exists():
                        raise ValueError(
                            "Interrupted paid pair requires reconciliation; no automatic replay"
                        )
                    settle(
                        state,
                        key,
                        json.loads((root / "report.json").read_text()),
                        envelope.reservation_usd,
                    )
                    write(path, state)
                archive_completed(root)
                continue
            if not plan.exists():
                code = invoke(
                    "m4_model_pilot.py",
                    [
                        "prepare",
                        "--config",
                        delivered / "config.json",
                        "--contract",
                        delivered / "contract.json",
                        "--root",
                        root,
                        "--output",
                        plan,
                        "--protocol",
                        args.protocol,
                        "--split",
                        "evaluation",
                        "--repeat",
                        repeat,
                        "--api-envelope",
                        "mini-160kb-v1",
                    ],
                    args.control / (key + ".log"),
                )
                if code:
                    raise ValueError("Pair preparation failed before model admission")
            admit(state, key, reservation)
            state["pairs"][key].update(
                root=str(root), prepared_sha256=json.loads(plan.read_text())["prepared_sha256"]
            )
            write(path, state)  # Durable intent precedes every possibly billable operation.
            print(
                json.dumps(
                    {
                        "task": name,
                        "repeat": repeat,
                        "phase": "models",
                        "exposure_usd": float(exposure(state)),
                    }
                ),
                flush=True,
            )
            code = invoke(
                "m4_model_pilot.py",
                [
                    "run",
                    "--prepared",
                    plan,
                    "--auth-file",
                    args.auth_file,
                    "--api-key-file",
                    args.api_key_file,
                    "--allow-model-calls",
                    "--allow-api-calls",
                    "--approved-budget-usd",
                    reservation,
                ],
                args.control / (key + ".log"),
            )
            report = json.loads((root / "report.json").read_text())
            settle(state, key, report, envelope.reservation_usd)
            state["pairs"][key].update(
                exit_code=code,
                report_sha256=hashlib.sha256((root / "report.json").read_bytes()).hexdigest(),
            )
            write(path, state)
            summary = summarize(args, protocol, state)
            print(
                json.dumps(
                    {
                        "task": name,
                        "repeat": repeat,
                        "phase": "settled",
                        "received": summary["received"],
                        "exposure_usd": float(exposure(state)),
                        "harness_status": report["status"],
                    }
                ),
                flush=True,
            )
            archive_completed(root)
        task_state["status"] = "completed"
        write(path, state)
        # Image archives are reproducible delivery caches; contracts and image hashes remain.
        archive = delivered / "images.tar"
        if archive.exists():
            write(
                delivered / "cache-released.json",
                {
                    "sha256": json.loads((delivered / "config.json").read_text())["images_sha256"],
                    "bytes": archive.stat().st_size,
                    "reason": "both assigned repeats have settled",
                },
            )
            archive.unlink()
    report = summarize(args, protocol, state)
    print(
        json.dumps(
            {
                "phase": "bounded_complete" if args.stop_after_task else "complete",
                "received": report["received"],
                "planned": report["planned"],
                "exposure_usd": float(exposure(state)),
            }
        ),
        flush=True,
    )
    expected = len(selected) * protocol["repeats"]["evaluation"] * len(protocol["branches"])
    return int(report["received"] != expected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for flag in (
        "config",
        "root",
        "control",
        "environments",
        "upstream",
        "auth-file",
        "api-key-file",
    ):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument(
        "--stop-after-task", help="Stop after this frozen task without changing the schedule"
    )
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--approved-total-usd", type=float, required=True)
    parser.add_argument("--prior-cost-usd", type=float, required=True)
    sys.exit(main(parser.parse_args()))
