"""Two explicitly approved subscription turns: committed native recovery and usage."""

import argparse
import json
import os
import subprocess
import sys
import time
from argparse import Namespace

from rtv.codex_agent import DEFAULT_MODEL, validate_auth
from rtv.runtime import checkpoint, sandbox
from rtv.runtime.cli import make_spec
from rtv.runtime.config import Settings, execution_dir
from rtv.runtime.control import reconcile, summary
from rtv.runtime.store import Store


def main(args):
    if not args.allow_model_calls:
        raise ValueError("--allow-model-calls required")
    auth = validate_auth(args.auth_file)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["RTV_RUNTIME_ROOT"] = str(output)
    settings = Settings.load()
    store = Store(settings)
    store.initialize()
    workers = []
    checks = {}
    row = None
    began = time.monotonic()

    def start():
        with (output / f"worker-{len(workers)}.log").open("wb") as log:
            p = subprocess.Popen(
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
        workers.append(p)
        return p

    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def wait(test, timeout=240):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if value := test():
                return value
            if row and store.get(row["trial_id"])["status"] in {"failed", "cancelled"}:
                raise RuntimeError("Real trial failed; inspect local supervisor logs")
            time.sleep(0.25)
        raise TimeoutError("Codex recovery did not converge")

    try:
        worker = start()
        spec = make_spec(
            Namespace(
                adapter="codex",
                ttl=420,
                delay=0,
                image=None,
                model=DEFAULT_MODEL,
                allow_model_calls=True,
                staged=True,
                max_recoveries=0,
            )
        )
        row = store.submit("m2-native-recovery", spec)
        first = wait(lambda: store.get(row["trial_id"])["latest_checkpoint"])
        source = store.checkpoint(first)
        check(
            "completed_native_checkpoint_committed",
            source["stage"] == 1 and source["state"] == "committed",
        )
        worker.kill()
        worker.wait(timeout=10)
        start()
        final = wait(
            lambda: r if (r := store.get(row["trial_id"]))["status"] == "succeeded" else None
        )
        history = store.executions(row["trial_id"])
        check(
            "fresh_execution_same_attempt",
            len(history) == 2 and final["epoch"] == 2 and final["attempt_id"] == row["attempt_id"],
        )
        last = store.checkpoint(final["latest_checkpoint"])
        check(
            "native_session_identity_preserved",
            source["context"]["result"]["session_id"] == last["context"]["result"]["session_id"],
        )
        check("final_verifier_passed", final["result"]["rewards"] == {"reward": 1.0})
        restored = execution_dir(settings, final) / "roundtrip"
        from rtv.harbor_probe import tree_digest

        check(
            "restored_workspace_matches_source",
            tree_digest(restored) == source["context"]["workspace_digest"],
        )
        ledger = store.ledger(row["trial_id"])
        check(
            "two_known_physical_calls",
            len(ledger["operations"]) == 2
            and ledger["unknown_operations"] == 0
            and ledger["pending_operations"] == 0,
        )
        check("prefix_counted_once", ledger["known_usage"] == final["result"]["session_usage"])
        check("dollar_cost_not_fabricated", ledger["cost_usd"] is None)
        check(
            "no_remaining_task_resources",
            all(not any(sandbox.resources(e).values()) for e in history),
        )
        check(
            "current_and_ancestor_checkpoints_gc_protected",
            checkpoint.collect(settings, 0)["deleted"] == [],
        )
    finally:
        if row and store.get(row["trial_id"])["cleanup"] != "complete":
            store.cancel(row["trial_id"])
            for _ in range(180):
                if reconcile(settings, row["trial_id"], launch=False)["cleanup"] == "complete":
                    break
                time.sleep(0.5)
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                worker.wait(timeout=10)
        report = {
            "status": "passed" if len(checks) == 10 and all(checks.values()) else "failed",
            "mode": "real-codex-stage-checkpoint-recovery",
            "checks": checks,
            "elapsed_seconds": round(time.monotonic() - began, 2),
            "trial": summary(store.get(row["trial_id"])) if row else None,
            "ledger": store.ledger(row["trial_id"]) if row else None,
        }
        (output / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--allow-model-calls", action="store_true")
    main(parser.parse_args())
