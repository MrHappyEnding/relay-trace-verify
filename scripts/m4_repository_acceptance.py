"""Real repository/runtime/mini scaffold with scripted reference controls; zero models."""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

from rtv.runtime import checkpoint, sandbox
from rtv.runtime.config import Settings, execution_dir
from rtv.runtime.control import reconcile
from rtv.runtime.handoff import plan
from rtv.runtime.relay_store import RelayStore
from rtv.runtime.tasks import make_repository_spec, validate_contract, workspace_digest
from rtv.runtime.views import experiment_view


def main(args):
    declared = validate_contract(json.loads(args.contract.read_text()))
    if declared["instance_id"] != "sympy__sympy-22914":
        raise ValueError("This scripted acceptance is pinned to the first development task")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for key in (
        "RTV_ALLOW_MODEL_CALLS",
        "RTV_ALLOW_API_CALLS",
        "RTV_CODEX_AUTH_FILE",
        "RTV_API_KEY_FILE",
    ):
        os.environ.pop(key, None)
    os.environ.update(RTV_RUNTIME_ROOT=str(output), RTV_SCHEMA="rtv3", RTV_MAX_EXECUTIONS="1")
    settings = Settings.load()
    store = RelayStore(settings)
    store.initialize()
    controls = output / "scripted-controls.json"
    # This is intentionally an oracle-derived control, never an agent prediction.
    oracle = (Path(declared["task_directory"]) / "solution/solve.sh").read_text()
    controls.write_text(
        json.dumps(
            {
                "scripted_source": [
                    "test ! -e /tests/test.sh && test ! -e /solution/solve.sh",
                    "mkdir .rtv-state && chmod 750 .rtv-state "
                    "&& printf preserved > .rtv-state/note "
                    "&& chmod 751 .rtv-state/note && mkdir .rtv-state/empty",
                ],
                "scripted_target": [
                    "test ! -e /tests/test.sh && test ! -e /solution/solve.sh",
                    "bash -c " + shlex.quote(oracle),
                ],
            }
        )
    )
    spec = make_repository_spec(
        Namespace(
            task_contract=args.contract,
            adapter="fixture",
            ttl=1800,
            agent_seconds=300,
            delay=0,
            pause_after_checkpoint=True,
            staged=True,
            image=None,
            max_recoveries=0,
            allow_model_calls=False,
            allow_api_calls=False,
            api_call_limit=8,
            api_budget_usd=0,
            model=None,
            scripted_controls=controls,
        )
    )
    checks, trials = {}, []
    started = time.monotonic()
    worker = None
    completed = False

    def check(name, value):
        checks[name] = bool(value)
        if not value:
            raise AssertionError(name)

    def wait(fn, seconds=900):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            value = fn()
            if value:
                return value
            time.sleep(0.4)
        raise TimeoutError("Repository acceptance did not converge")

    def until_status(trial, status):
        def poll():
            row = store.get(trial)
            if row["status"] in {"failed", "cancelled"}:
                raise RuntimeError("Repository execution failed: " + json.dumps(row["result"]))
            return row if row["status"] == status else None

        return wait(poll)

    try:
        with (output / "worker.log").open("wb") as log:
            worker = subprocess.Popen(
                [sys.executable, "-m", "rtv", "runtime", "worker"], stdout=log, stderr=log
            )
        source = store.submit("repository-prefix", spec)
        trials.append(source)
        source = until_status(source["trial_id"], "sealed")
        cp = store.checkpoint(source["latest_checkpoint"])
        bundle = execution_dir(settings, source) / "bundle"
        source_digest = workspace_digest(bundle / "workspace", spec)
        check(
            "source_sealed_without_hidden_reward",
            "rewards" not in source["result"]
            and source["result"]["verification"]["status"] == "not_run",
        )
        check(
            "source_tests_not_mounted",
            not list((execution_dir(settings, source) / "sandbox/verifier").iterdir()),
        )
        check(
            "manifest_v3_directory_modes",
            checkpoint.artifacts(settings).load(checkpoint.reference(cp)).schema_version == 3,
        )
        check(
            "contract_bound_to_checkpoint",
            cp["context"]["task_contract_sha256"] == declared["contract_sha256"],
        )
        check("source_resources_removed", not any(sandbox.resources(source).values()))
        branches = plan(spec, adapter="mini-fixture", ttl=1800)
        a = branches[0]["spec"]
        a["max_recoveries"] = 1
        a["scripted_target"] = [
            "test ! -e .rtv-inflight && touch .rtv-inflight && sleep 20 && rm .rtv-inflight",
            *a["scripted_target"],
        ]
        eid = store.create_experiment(source, "repository-paired", branches, 1800)
        children = store.experiment(eid)["trials"]
        trials.extend(children)
        a_row = next(r for r in children if r["branch_label"] == "A-continue")

        def in_flight():
            row = store.get(a_row["trial_id"])
            if row["status"] == "failed":
                raise RuntimeError("A failed before injection: " + json.dumps(row["result"]))
            ids = sandbox.project_resources(sandbox.session(row))["containers"]
            for cid in ids:
                state = subprocess.run(
                    ["docker", "exec", cid, "test", "-f", "/testbed/.rtv-inflight"],
                    capture_output=True,
                )
                if state.returncode == 0:
                    return row
            return None

        fault = wait(in_flight)
        pid = json.loads((execution_dir(settings, fault) / "started.json").read_text())["pid"]
        os.kill(pid, signal.SIGKILL)
        recovered = wait(
            lambda: r if (r := store.get(fault["trial_id"]))["epoch"] > fault["epoch"] else None
        )
        check(
            "fork_can_recover_from_inherited_checkpoint",
            recovered["resume_from"] == cp["checkpoint_id"],
        )
        check("recovery_keeps_original_deadline", recovered["deadline"] == fault["deadline"])
        finals = [until_status(r["trial_id"], "succeeded") for r in children]
        check(
            "three_continuations_pass_offline",
            all(r["result"]["rewards"] == {"reward": 1.0} for r in finals),
        )
        check(
            "mini_scaffold_is_explicitly_scripted",
            all(
                r["result"]["synthetic_model"]
                for r in finals
                if r["spec"]["adapter"] == "mini-fixture"
            ),
        )
        for row in finals:
            restored = execution_dir(settings, row) / "restored/workspace"
            check(
                row["branch_label"] + "_restores_exact_prefix",
                workspace_digest(restored, spec) == source_digest,
            )
            check(
                row["branch_label"] + "_verifier_receives_exact_snapshot",
                row["result"]["workspace_digest"]
                == row["result"]["verification"]["workspace_digest"],
            )
            check(
                row["branch_label"] + "_preserves_directory_mode",
                (restored / ".rtv-state").stat().st_mode & 0o777 == 0o750,
            )
        new_a = next(r for r in finals if r["branch_label"] == "A-continue")
        check(
            "uncommitted_fault_marker_discarded",
            not (execution_dir(settings, new_a) / "restored/workspace/.rtv-inflight").exists(),
        )
        check(
            "interrupted_script_operation_unknown",
            store.ledger(new_a["trial_id"])["unknown_operations"] == 1,
        )
        check(
            "source_checkpoint_unchanged",
            workspace_digest(bundle / "workspace", spec) == source_digest,
        )
        fresh_spec = {
            **spec,
            "adapter": "mini-fixture",
            "stages": 1,
            "pause_after_checkpoint": False,
            "api_call_limit": 16,
            "agent_seconds": 600,
        }
        fresh = store.submit("repository-fresh", fresh_spec)
        trials.append(fresh)
        fresh = until_status(fresh["trial_id"], "succeeded")
        check(
            "fresh_baseline_has_no_prefix",
            fresh["resume_from"] is None
            and not store.ledger(fresh["trial_id"])["prefix_operation_ids"],
        )
        check(
            "fresh_does_not_inherit_source_files",
            not (execution_dir(settings, fresh) / "bundle/workspace/.rtv-state").exists(),
        )
        check(
            "all_verifiers_are_isolated",
            all(r["result"]["verification"]["isolated"] for r in [*finals, fresh]),
        )
        check(
            "source_hidden_tests_not_in_checkpoint",
            not (bundle / "workspace/tests/test.sh").exists(),
        )
        (output / "experiment.json").write_text(
            json.dumps(experiment_view(settings, eid), indent=2, default=str)
        )
        (output / "fresh.json").write_text(json.dumps(fresh, indent=2, default=str))
        completed = True
    finally:
        if worker and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=15)
        for trial in trials:
            row = store.get(trial["trial_id"])
            if row["status"] not in {"succeeded", "failed", "cancelled"}:
                store.cancel(row["trial_id"])
            reconcile(settings, row["trial_id"], launch=False)
            for execution in store.executions(row["trial_id"]):
                sandbox.cleanup(execution)
        checks["all_owned_resources_removed"] = all(
            not any(sandbox.resources(e).values())
            for r in trials
            for e in store.executions(r["trial_id"])
        )
        report = {
            "status": "passed" if completed and all(checks.values()) else "failed",
            "mode": "real-repository-services-and-mini-scaffold-scripted-oracle-controls",
            "task": declared["instance_id"],
            "contract_sha256": declared["contract_sha256"],
            "paid_api_calls": 0,
            "model_calls": 0,
            "cloud_spend_usd": 0,
            "not_tested": [
                "real model outcomes",
                "real Codex native repository session",
                "Kubernetes repository execution",
            ],
            "checks": checks,
            "trial_ids": [r["trial_id"] for r in trials],
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
