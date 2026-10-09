"""Opt-in real Codex turn across a control-worker SIGKILL. Uses existing subscription."""

import argparse
import json
import os
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

from rtv.codex_agent import DEFAULT_MODEL, validate_auth
from rtv.runtime import sandbox
from rtv.runtime.cli import make_spec
from rtv.runtime.config import Settings, execution_dir
from rtv.runtime.control import reconcile, summary
from rtv.runtime.store import Store


def main(args):
    if not args.allow_model_calls:
        raise ValueError("Explicit --allow-model-calls required (subscription usage)")
    auth = validate_auth(args.auth_file)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["RTV_RUNTIME_ROOT"] = str(output)
    settings = Settings.load()
    store = Store(settings)
    store.initialize()
    workers = []
    checks = {}
    began = time.monotonic()
    row = None

    def check(name, value):
        checks[name] = bool(value)
        if not value:
            raise AssertionError(name)

    def start_worker():
        with (output / f"worker-{len(workers)}.log").open("wb") as log:
            worker = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "rtv",
                    "runtime",
                    "worker",
                    "--allow-model-calls",
                    "--auth-file",
                    str(auth),
                ],
                stdout=log,
                stderr=log,
            )
        workers.append(worker)
        return worker

    def wait(predicate, timeout=180):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if value := predicate():
                return value
            time.sleep(0.2)
        raise TimeoutError("Real Codex smoke did not converge")

    try:
        first = start_worker()
        spec = make_spec(
            Namespace(
                adapter="codex",
                ttl=240,
                delay=0,
                image=None,
                model=DEFAULT_MODEL,
                allow_model_calls=True,
            )
        )
        row = store.submit("codex-worker-restart", spec)
        logs = execution_dir(settings, row) / "sandbox/agent/codex.txt"
        wait(lambda: logs.is_file() and '"thread.started"' in logs.read_text())
        check("codex_turn_active_before_restart", store.get(row["trial_id"])["status"] == "running")
        before = sandbox.resources(row)
        first.kill()
        first.wait(timeout=10)
        check("same_sandbox_after_worker_sigkill", before == sandbox.resources(row))
        start_worker()
        duplicate = store.submit("codex-worker-restart", spec)
        check(
            "duplicate_real_request_same_execution",
            duplicate["execution_id"] == row["execution_id"],
        )
        wait(lambda: store.get(row["trial_id"])["cleanup"] == "complete")
        final = store.get(row["trial_id"])
        check(
            "real_codex_task_verified",
            final["status"] == "succeeded" and final["result"]["rewards"] == {"reward": 1.0},
        )
        starts = [
            json.loads(line)
            for line in logs.read_text().splitlines()
            if line.startswith('{"type":"thread.started"')
        ]
        check("one_native_session_start", len(starts) == 1)
        check("observed_usage_preserved", final["result"]["usage_status"] == "known")
        check("no_remaining_task_resources", not any(sandbox.resources(row).values()))
    finally:
        if row is not None and store.get(row["trial_id"])["cleanup"] != "complete":
            store.cancel(row["trial_id"])
            wait(
                lambda: reconcile(settings, row["trial_id"], launch=False)["cleanup"] == "complete"
            )
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=10)
        report = {
            "mode": "real-codex-temporal-worker-restart",
            "checks": checks,
            "status": "passed" if len(checks) == 7 and all(checks.values()) else "failed",
            "elapsed_seconds": round(time.monotonic() - began, 2),
            "trial": summary(store.get(row["trial_id"])) if row else None,
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--allow-model-calls", action="store_true")
    main(parser.parse_args())
