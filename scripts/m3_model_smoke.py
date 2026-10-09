"""Opt-in real paired acceptance: two subscription turns and three bounded API branches."""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

from rtv.codex_agent import DEFAULT_MODEL, validate_auth
from rtv.runtime import sandbox
from rtv.runtime.cli import make_spec
from rtv.runtime.config import Settings
from rtv.runtime.control import reconcile
from rtv.runtime.handoff import MINI_MODEL, plan
from rtv.runtime.relay_store import RelayStore
from rtv.runtime.views import experiment_view


def main(args):
    if (
        not args.allow_model_calls
        or not args.allow_api_calls
        or not math.isfinite(args.approved_budget_usd)
        or args.approved_budget_usd <= 0
    ):
        raise ValueError(
            "Requires both model opt-ins and a positive finite approved budget after owner approval"
        )
    from rtv.mini_agent import RESERVATION_USD

    if (
        not 1 <= args.api_call_limit <= 8
        or not math.isfinite(args.prior_reserved_usd)
        or not 0 <= args.prior_reserved_usd < args.approved_budget_usd
    ):
        raise ValueError("Invalid bounded rerun allowance")
    if (
        args.prior_reserved_usd + 3 * args.api_call_limit * RESERVATION_USD
        > args.approved_budget_usd
    ):
        raise ValueError("Prior reservations plus this run would exceed the approved total")
    auth = validate_auth(args.auth_file)
    if (
        args.api_key_file.is_symlink()
        or not args.api_key_file.is_file()
        or not args.api_key_file.read_text().strip()
    ):
        raise ValueError(
            "A nonempty local API key file is required; never include its content in reports"
        )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["RTV_RUNTIME_ROOT"] = str(output)
    settings = Settings.load()
    store = RelayStore(settings)
    store.initialize()
    checks = {}
    source = None
    eid = None
    completed = False
    began = time.monotonic()

    def check(name, value):
        checks[name] = bool(value)
        if not value:
            raise AssertionError(name)

    def wait(fn, seconds=600):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if result := fn():
                return result
            time.sleep(0.5)
        raise TimeoutError("Real M3 test did not converge; no automatic retry")

    def sealed():
        r = store.get(source["trial_id"])
        if r["status"] in {"failed", "cancelled"}:
            raise RuntimeError("Source failed; no retry")
        return r if r["status"] == "sealed" else None

    with (output / "worker.log").open("wb") as log:
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
                "--allow-api-calls",
                "--api-key-file",
                str(args.api_key_file.resolve()),
            ],
            stdout=log,
            stderr=log,
        )
    try:
        spec = make_spec(
            Namespace(
                adapter="codex",
                ttl=900,
                delay=0,
                image=None,
                model=DEFAULT_MODEL,
                allow_model_calls=True,
                staged=True,
                max_recoveries=0,
                pause_after_checkpoint=True,
            )
        )
        source = store.submit("m3-real-source", spec)
        source = wait(sealed)
        cp = store.checkpoint(source["latest_checkpoint"])
        check("real_source_sealed", cp["context"]["adapter"] == "codex")
        branches = plan(spec, adapter="mini", model=MINI_MODEL, portable=True, ttl=600)
        for branch in branches:
            if branch["spec"]["adapter"] == "mini":
                branch["spec"]["api_call_limit"] = args.api_call_limit
        eid = store.create_experiment(source, "m3-real-paired", branches, 600)

        def finished():
            rs = store.experiment(eid)["trials"]
            return (
                rs if all(r["status"] in {"succeeded", "failed", "cancelled"} for r in rs) else None
            )

        rows = wait(finished)
        report = experiment_view(settings, eid)
        (output / "experiment.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        check(
            "four_branches_verified",
            all(
                r["status"] == "succeeded" and r["result"]["rewards"] == {"reward": 1.0}
                for r in rows
            ),
        )
        check(
            "one_common_source_checkpoint",
            all(r["parent_checkpoint"] == cp["checkpoint_id"] for r in rows),
        )
        native = next(r for r in rows if r["branch_label"] == "A-continue")
        check(
            "native_codex_session_preserved",
            native["result"]["session_id"] == cp["context"]["result"]["session_id"],
        )
        check(
            "three_real_mini_branches",
            sum(r["spec"]["adapter"] == "mini" and not r["result"]["synthetic_model"] for r in rows)
            == 3,
        )
        check("all_verifiers_isolated", all(r["result"]["verification"]["isolated"] for r in rows))
        ledger = [store.ledger(r["trial_id"]) for r in rows]
        api_ops = [o for entry in ledger for o in entry["operations"] if o["kind"] == "mini-api"]
        check("api_calls_bounded_without_retries", 3 <= len(api_ops) <= 3 * args.api_call_limit)
        check("api_usage_known", all(o["state"] == "known" and o["usage"] for o in api_ops))
        check(
            "api_cost_within_approved_budget",
            args.prior_reserved_usd + sum(o["cost_usd"] for o in api_ops)
            <= args.approved_budget_usd,
        )
        check(
            "reserved_cost_within_approved_budget",
            args.prior_reserved_usd + sum(o["reserved_usd"] for o in api_ops)
            <= args.approved_budget_usd,
        )
        prefix = store.ledger(source["trial_id"])["known_usage"]
        check(
            "same_prefix_attributed_to_each_policy",
            all(entry["prefix"]["known_usage"] == prefix for entry in ledger),
        )
        check(
            "native_prefix_not_double_counted",
            store.ledger(native["trial_id"])["end_to_end"]["known_usage"]
            == native["result"]["session_usage"],
        )
        portable = next(r for r in report["trials"] if r["label"] == "B-trajectory")
        check(
            "cross_framework_context_loss_declared",
            bool(portable["handoffs"][0]["prepared"]["losses"]),
        )
        check(
            "all_branch_resources_cleaned",
            all(
                not any(sandbox.resources(e).values())
                for r in rows
                for e in store.executions(r["trial_id"])
            ),
        )
        completed = True
    finally:
        rows = store.experiment(eid)["trials"] if eid else []
        for r in ([source] if source else []) + rows:
            current = store.get(r["trial_id"])
            if current["status"] not in {"succeeded", "failed", "cancelled"}:
                store.cancel(r["trial_id"])
                wait(
                    lambda r=r: (
                        reconcile(settings, r["trial_id"], launch=False)["cleanup"] == "complete"
                    ),
                    120,
                )
        worker.terminate()
        worker.wait(timeout=10)
        report = {
            "status": "passed" if completed else "failed",
            "mode": "real-codex-and-mini-swe-paired-development-task",
            "checks": checks,
            "experiment_id": eid,
            "approved_api_budget_usd": args.approved_budget_usd,
            "prior_reserved_usd": args.prior_reserved_usd,
            "api_call_limit_per_branch": args.api_call_limit,
            "model": MINI_MODEL,
            "source_model": DEFAULT_MODEL,
            "elapsed_seconds": round(time.monotonic() - began, 2),
            "warning": (
                "Models and scaffolds both change; no causal scaffold or success-rate conclusion."
            ),
        }
        if eid:
            details = experiment_view(settings, eid)
            (output / "experiment.json").write_text(
                json.dumps(details, indent=2, default=str) + "\n"
            )
            report["observed_api_cost_usd"] = sum(
                r["ledger"]["known_api_cost_usd"] for r in details["trials"]
            )
            report["unknown_operations"] = sum(
                r["ledger"]["unknown_operations"] for r in details["trials"]
            )
            api_ops = [
                o
                for r in rows
                for o in store.ledger(r["trial_id"])["operations"]
                if o["kind"] == "mini-api"
            ]
            report["api_requests"] = len(api_ops)
            report["unknown_api_requests"] = sum(o["state"] != "known" for o in api_ops)
            report["api_reserved_usd"] = round(sum(o["reserved_usd"] for o in api_ops), 9)
            report["cumulative_reserved_usd"] = round(
                args.prior_reserved_usd
                + sum(max(o["reserved_usd"], o["cost_usd"] or 0) for o in api_ops),
                9,
            )
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth-file", type=Path, required=True)
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--allow-api-calls", action="store_true")
    parser.add_argument("--approved-budget-usd", type=float, required=True)
    parser.add_argument("--prior-reserved-usd", type=float, default=0)
    parser.add_argument("--api-call-limit", type=int, default=8)
    main(parser.parse_args())
