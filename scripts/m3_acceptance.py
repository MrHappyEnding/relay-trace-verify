"""Real services and mini-swe loop with a SCRIPTED model; zero paid API calls."""

import argparse
import json
import os
import subprocess
import sys
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from rtv.harbor_probe import tree_digest
from rtv.runtime import checkpoint, sandbox
from rtv.runtime.cli import make_spec
from rtv.runtime.config import Settings, execution_dir
from rtv.runtime.control import reconcile
from rtv.runtime.handoff import context_path, load_context, plan, prepare, target_spec
from rtv.runtime.relay_store import RelayStore
from rtv.runtime.store import Fenced
from rtv.runtime.views import experiment_view


def main(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["RTV_RUNTIME_ROOT"] = str(output)
    settings = Settings.load()
    store = RelayStore(settings)
    store.initialize()
    checks = {}
    rows = []
    workers = []
    completed = False
    began = time.monotonic()

    def check(name, value):
        checks[name] = bool(value)
        if not value:
            raise AssertionError(name)

    def rejected(name, fn, error=ValueError):
        try:
            fn()
        except error:
            check(name, True)
        else:
            check(name, False)

    def wait(fn, seconds=180):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if value := fn():
                return value
            time.sleep(0.3)
        raise TimeoutError("M3 did not converge")

    def start():
        with (output / f"worker-{len(workers)}.log").open("wb") as log:
            p = subprocess.Popen(
                [sys.executable, "-m", "rtv", "runtime", "worker"], stdout=log, stderr=log
            )
        workers.append(p)
        return p

    def stop(p):
        p.kill()
        p.wait(timeout=10)

    def submit(key):
        spec = make_spec(
            Namespace(
                adapter="fixture",
                ttl=900,
                delay=0,
                image=None,
                model=None,
                allow_model_calls=False,
                staged=True,
                max_recoveries=0,
                pause_after_checkpoint=True,
            )
        )
        row = store.submit(key, spec)
        rows.append(row)
        return row

    try:
        worker = start()
        source = submit("source")
        source = wait(
            lambda: r if (r := store.get(source["trial_id"]))["status"] == "sealed" else None
        )
        cp = store.checkpoint(source["latest_checkpoint"])
        check(
            "source_sealed_after_independent_verification",
            source["result"]["verification"]["isolated"],
        )
        check("source_sandbox_removed_before_handoff", not any(sandbox.resources(source).values()))
        stop(worker)
        branches = plan(source["spec"], adapter="mini-fixture", portable=True, ttl=600)
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(
                pool.map(
                    lambda _: store.create_experiment(source, "paired", branches, 600), range(2)
                )
            )
        eid = ids[0]
        ex = store.experiment(eid)
        children = ex["trials"]
        rows.extend(children)
        check("concurrent_duplicate_fork_same_identity", ids[0] == ids[1] and len(children) == 4)
        check(
            "branch_trials_and_attempts_are_distinct",
            len({r["trial_id"] for r in children}) == 4
            and len({r["attempt_id"] for r in children}) == 4,
        )
        check(
            "every_branch_uses_same_committed_prefix",
            all(r["parent_checkpoint"] == cp["checkpoint_id"] for r in children),
        )
        check("equal_frozen_branch_ttl", all(r["spec"]["ttl_seconds"] == 600 for r in children))
        rejected(
            "changed_experiment_configuration_rejected",
            lambda: store.create_experiment(source, "paired", branches, 500),
        )
        first = children[0]
        hid = first["handoff_id"]
        rejected("held_target_not_admitted", lambda: store.begin_call(first), Fenced)

        def lost_before_activation(*args):
            raise RuntimeError("simulated process loss after prepare")

        with patch.object(RelayStore, "activate", lost_before_activation):
            rejected("prepare_response_loss_injected", lambda: prepare(settings, hid), RuntimeError)
        check("target_preparation_durable", store.handoff(hid)["state"] == "prepared")
        check(
            "no_call_before_authority_transfer", store.ledger(first["trial_id"])["operations"] == []
        )
        check(
            "active_branch_references_protect_source",
            cp["checkpoint_id"] not in checkpoint.collect(settings, 0)["deleted"],
        )
        worker = start()
        finals = wait(
            lambda: (
                rs
                if (rs := store.experiment(eid)["trials"])
                and all(r["status"] == "succeeded" for r in rs)
                else None
            )
        )
        check(
            "all_four_branches_independently_pass",
            all(r["result"]["rewards"] == {"reward": 1.0} for r in finals),
        )
        check(
            "verifier_receives_exact_declared_workspace",
            all(
                r["result"]["verification"]["workspace_digest"] == r["result"]["workspace_digest"]
                for r in finals
            ),
        )
        check(
            "verifier_containers_are_distinct",
            len({r["result"]["verification"]["container"] for r in finals}) == 4,
        )
        check(
            "scripted_model_not_reported_as_real",
            all(
                r["result"].get("synthetic_model")
                for r in finals
                if r["spec"]["adapter"] == "mini-fixture"
            ),
        )
        check(
            "upstream_mini_loop_executed_three_steps",
            all(
                r["result"].get("model_requests") == 3
                for r in finals
                if r["spec"]["adapter"] == "mini-fixture"
            ),
        )
        check(
            "one_stage_admission_per_branch",
            all(len(store.ledger(r["trial_id"])["operations"]) == 1 for r in finals),
        )
        check(
            "preparation_retry_did_not_duplicate_execution",
            all(len(store.executions(r["trial_id"])) == 1 for r in finals),
        )
        context = {r["branch_label"]: load_context(settings, r) for r in finals}
        check("workspace_policy_has_no_summary_or_trajectory", context["B-workspace"]["text"] == "")
        check(
            "summary_and_portable_contexts_are_distinct",
            context["B-summary"]["text"] != context["B-trajectory"]["text"]
            and bool(context["B-summary"]["text"]),
        )
        check(
            "portable_context_declares_projection_losses", bool(context["B-trajectory"]["losses"])
        )
        check(
            "handoff_contains_no_hidden_verifier_feedback",
            all(
                '"rewards"' not in c["text"] and '"verification": {"isolated"' not in c["text"]
                for c in context.values()
            ),
        )
        restored = [execution_dir(settings, r) / "roundtrip" for r in finals]
        check(
            "all_initial_workspace_digests_match",
            all(tree_digest(p) == cp["context"]["workspace_digest"] for p in restored),
        )
        (restored[0] / "branch-only.txt").write_text("mutation")
        check(
            "branch_file_mutation_does_not_leak",
            all(not (p / "branch-only.txt").exists() for p in restored[1:]),
        )
        check(
            "source_snapshot_unmodified",
            store.checkpoint(cp["checkpoint_id"])["manifest_sha256"] == cp["manifest_sha256"],
        )
        prefix = store.ledger(source["trial_id"])["operations"]
        check(
            "prefix_operation_ids_reused_not_copied",
            all(
                store.ledger(r["trial_id"])["prefix_operation_ids"]
                == [p["operation_id"] for p in prefix]
                for r in finals
            ),
        )
        check(
            "zero_paid_api_operations",
            all(
                not any(o["kind"] == "mini-api" for o in store.ledger(r["trial_id"])["operations"])
                for r in finals
            ),
        )
        stop(worker)
        target = target_spec(source["spec"], "mini-fixture", None, "summary", ttl=900)
        h = store.request(source, "relay", target, "summary")
        check(
            "handoff_request_idempotent",
            store.request(source, "relay", target, "summary")["handoff_id"] == h["handoff_id"],
        )
        rejected(
            "conflicting_handoff_key_rejected",
            lambda: store.request(source, "relay", target, "task-only"),
        )
        original_activate = RelayStore.activate

        def lost_after_activation(self, hid):
            original_activate(self, hid)
            raise RuntimeError("activation committed, reply lost")

        with patch.object(RelayStore, "activate", lost_after_activation):
            state = reconcile(settings, source["trial_id"], launch=False)
        current = store.get(source["trial_id"])
        check(
            "activation_reply_loss_converges",
            state["status"] == "queued" and store.handoff(h["handoff_id"])["state"] == "completed",
        )
        check(
            "relay_keeps_trial_and_creates_attempt",
            current["trial_id"] == source["trial_id"]
            and current["attempt_id"] != source["attempt_id"]
            and current["epoch"] == 2,
        )
        rejected(
            "old_attempt_cannot_publish",
            lambda: store.finish(source, {"status": "failed", "reason": "late"}, controller=True),
            Fenced,
        )
        worker = start()
        relayed = wait(
            lambda: r if (r := store.get(source["trial_id"]))["status"] == "succeeded" else None
        )
        check("same_trial_relay_verified", relayed["result"]["rewards"] == {"reward": 1.0})
        check(
            "relay_ledger_keeps_both_stages",
            len(store.ledger(source["trial_id"])["operations"]) == 2,
        )
        cancelled = submit("cancel-source")
        cancelled = wait(
            lambda: r if (r := store.get(cancelled["trial_id"]))["status"] == "sealed" else None
        )
        stop(worker)
        bad = store.request(
            cancelled, "bad-target", {**target, "image_id": "sha256:" + "0" * 64}, "summary"
        )
        result = reconcile(settings, cancelled["trial_id"], launch=False)
        check(
            "preparation_failure_keeps_source_authority",
            result["status"] == "sealed"
            and result["epoch"] == 1
            and store.handoff(bad["handoff_id"])["state"] == "aborted",
        )
        h2 = store.request(cancelled, "cancel-preparing", target, "summary")
        with patch.object(RelayStore, "activate", lost_before_activation):
            rejected(
                "cancel_test_reaches_prepared",
                lambda: prepare(settings, h2["handoff_id"]),
                RuntimeError,
            )
        prepared_file = context_path(settings, h2["handoff_id"])
        original = prepared_file.read_bytes()
        prepared_file.write_bytes(original + b" ")
        rejected(
            "prepared_context_corruption_rejected", lambda: prepare(settings, h2["handoff_id"])
        )
        check(
            "corrupt_preparation_cannot_transfer_authority",
            store.get(cancelled["trial_id"])["epoch"] == 1,
        )
        prepared_file.write_bytes(original)
        store.cancel(cancelled["trial_id"])
        rejected("cancel_wins_before_activation", lambda: store.activate(h2["handoff_id"]), Fenced)
        reconcile(settings, cancelled["trial_id"], launch=False)
        check(
            "cancelled_handoff_never_starts_target",
            len(store.ledger(cancelled["trial_id"])["operations"]) == 1
            and store.handoff(h2["handoff_id"])["state"] == "aborted",
        )

        # Synthetic request reservations and usage, real database, no network request.
        synthetic = store.submit(
            "synthetic-api-ledger",
            {**target, "adapter": "mini", "api_call_limit": 2, "api_budget_usd": 0.124},
        )
        rows.append(synthetic)
        store.claim(synthetic)
        op = store.begin_call(synthetic)
        request = store.begin_model_call(synthetic, op, 1, 0.062)
        store.settle_call(
            synthetic, request, {"input_tokens": 100, "output_tokens": 10}, cost_usd=0.00012
        )
        second = store.begin_model_call(synthetic, op, 2, 0.062)
        rejected(
            "budget_prevents_third_request", lambda: store.begin_model_call(synthetic, op, 3, 0.062)
        )
        store.finish(synthetic, {"status": "failed", "reason": "synthetic-loss"}, controller=True)
        ledger = store.ledger(synthetic["trial_id"])
        check(
            "known_usage_retained_with_unknown_request",
            ledger["known_usage"]["input_tokens"] == 100
            and ledger["unknown_operations"] >= 1
            and ledger["cost_usd"] is None,
        )
        store.settle_call(
            synthetic, second, {"input_tokens": 50, "output_tokens": 5}, cost_usd=0.00006
        )
        check(
            "late_api_observation_counted_once",
            store.ledger(synthetic["trial_id"])["known_usage"]["input_tokens"] == 150,
        )
        check(
            "late_request_does_not_change_terminal_status",
            store.get(synthetic["trial_id"])["status"] == "failed",
        )
        report_view = experiment_view(settings, eid)
        (output / "experiment.json").write_text(
            json.dumps(report_view, indent=2, default=str) + "\n"
        )
        check(
            "report_links_source_handoffs_cost_and_results",
            all(
                r["handoffs"] and r["events"] and "end_to_end" in r["ledger"]
                for r in report_view["trials"]
            ),
        )
        check(
            "all_task_and_verifier_resources_removed",
            all(
                not any(sandbox.resources(e).values())
                for row in rows
                for e in store.executions(row["trial_id"])
            ),
        )
        completed = True
    finally:
        for p in workers:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=10)
        for row in rows:
            r = store.get(row["trial_id"])
            if r["status"] not in {"succeeded", "failed", "cancelled"}:
                store.cancel(r["trial_id"])
                reconcile(settings, r["trial_id"], launch=False)
        report = {
            "status": "passed" if completed and all(checks.values()) else "failed",
            "mode": "real-services-mini-scaffold-scripted-model-plus-synthetic-ledger",
            "paid_api_calls": 0,
            "checks": checks,
            "elapsed_seconds": round(time.monotonic() - began, 2),
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
