"""Real Temporal/PostgreSQL/Docker recovery, plus labeled synthetic protocol faults."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from temporalio.client import Client
from temporalio.worker import Replayer

from rtv.domain import ExecutionRef
from rtv.harbor_probe import tree_digest
from rtv.runtime import checkpoint, sandbox
from rtv.runtime.cli import make_spec
from rtv.runtime.config import Settings, execution_dir
from rtv.runtime.control import reconcile, recover, summary
from rtv.runtime.store import Fenced, Store
from rtv.runtime.workflow import TrialWorkflow


def wait(test, timeout=100):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if result := test():
            return result
        time.sleep(0.3)
    raise TimeoutError("M2 acceptance did not converge")


def main(output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["RTV_RUNTIME_ROOT"] = str(output)
    settings = Settings.load()
    store = Store(settings)
    store.initialize()
    checks = {}
    rows = []
    paused = None
    worker = None
    completed = False
    began = time.monotonic()

    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def rejected(name, fn, exception=ValueError):
        try:
            fn()
        except exception:
            check(name, True)
        else:
            check(name, False)

    def submit(key, delay=0):
        spec = make_spec(
            Namespace(
                adapter="fixture",
                ttl=300,
                delay=delay,
                image=None,
                model=None,
                allow_model_calls=False,
                staged=True,
                max_recoveries=2,
            )
        )
        row = store.submit(key, spec)
        rows.append(row)
        return row

    def current_event(row, kind):
        return next(
            (
                e
                for e in store.events(row["trial_id"])
                if e["kind"] == kind and e["payload"]["execution_id"] == row["execution_id"]
            ),
            None,
        )

    try:
        with (output / "worker.log").open("wb") as log:
            worker = subprocess.Popen(
                [sys.executable, "-m", "rtv", "runtime", "worker"], stdout=log, stderr=log
            )
        row = submit("lease-loss-real-containers", delay=22)
        old = wait(
            lambda: (
                r
                if (r := store.get(row["trial_id"]))["epoch"] == 2
                and current_event(r, "agent.started")
                else None
            )
        )
        source = store.checkpoint(old["resume_from"])
        original_digest = source["manifest_sha256"]
        source_ids = sandbox.resources(old)["containers"]
        paused = json.loads((execution_dir(settings, old) / "started.json").read_text())["pid"]
        os.kill(paused, signal.SIGSTOP)
        new = wait(
            lambda: r if (r := store.get(row["trial_id"]))["epoch"] == 3 else None, timeout=60
        )
        check(
            "lease_expiry_created_new_epoch",
            new["attempt_id"] == old["attempt_id"] and new["execution_id"] != old["execution_id"],
        )
        check("ttl_not_reset", new["deadline"] == old["deadline"])
        check(
            "lost_operation_is_unknown_before_old_return",
            store.ledger(row["trial_id"])["unknown_operations"] == 1,
        )
        rejected("old_phase_fenced", lambda: store.phase(old, "verifying", {}), Fenced)
        rejected(
            "old_result_fenced",
            lambda: store.finish(old, {"status": "failed", "reason": "stale"}),
            Fenced,
        )
        rejected(
            "old_checkpoint_fenced",
            lambda: store.checkpoint_commit(
                old, source["checkpoint_id"], source["manifest_sha256"], source["context"]
            ),
            Fenced,
        )
        wait(lambda: current_event(store.get(row["trial_id"]), "workspace.restored"))
        new = store.get(row["trial_id"])
        restored = execution_dir(settings, new) / "roundtrip"
        check(
            "restored_files_match_committed_workspace",
            tree_digest(restored) == source["context"]["workspace_digest"],
        )
        check("uncommitted_changes_discarded", not (restored / "uncommitted.txt").exists())
        check(
            "sandbox_identity_changed",
            not set(source_ids) & set(sandbox.resources(new)["containers"]),
        )
        os.kill(paused, signal.SIGCONT)
        paused = None
        final = wait(
            lambda: r if (r := store.get(row["trial_id"]))["status"] == "succeeded" else None
        )
        check(
            "old_return_cannot_replace_current_result",
            final["epoch"] == 3 and final["result"]["rewards"] == {"reward": 1.0},
        )
        check(
            "source_checkpoint_unchanged",
            store.checkpoint(source["checkpoint_id"])["manifest_sha256"] == original_digest,
        )
        check(
            "no_execution_resources_remain",
            all(not any(sandbox.resources(e).values()) for e in store.executions(row["trial_id"])),
        )

        async def history():
            client = await Client.connect(settings.temporal)
            handle = client.get_workflow_handle("rtv3-" + row["trial_id"])
            result = await asyncio.wait_for(handle.result(), 45)
            hist = await handle.fetch_history()
            await Replayer(workflows=[TrialWorkflow]).replay_workflow(hist)
            (output / "temporal-history.json").write_text(hist.to_json())
            check("workflow_replay_and_final_epoch", result["epoch"] == 3)

        asyncio.run(history())
        manual = submit("manual-recovery-after-container-loss", delay=10)
        broken = wait(
            lambda: (
                r
                if (r := store.get(manual["trial_id"]))["epoch"] == 2
                and current_event(r, "agent.started")
                else None
            )
        )
        from rtv.harbor_probe import _container_ids, _docker

        main_ids = _container_ids(sandbox.session(broken), main_only=True)
        _docker("rm", "-f", *main_ids)
        failed = wait(
            lambda: r if (r := store.get(manual["trial_id"]))["status"] == "failed" else None
        )

        async def closed_controller():
            client = await Client.connect(settings.temporal)
            handle = client.get_workflow_handle("rtv3-" + manual["trial_id"])
            await asyncio.wait_for(handle.result(), 30)
            return (await handle.describe()).run_id

        previous_run = asyncio.run(closed_controller())
        recover(settings, failed, "manual-after-loss")
        repaired = wait(
            lambda: r if (r := store.get(manual["trial_id"]))["status"] == "succeeded" else None
        )
        check(
            "destroyed_container_recovers_committed_stage",
            repaired["epoch"] == 3
            and repaired["result"]["restored_checkpoint"] == broken["resume_from"],
        )
        check(
            "manual_recovery_restarts_closed_controller",
            asyncio.run(closed_controller()) != previous_run,
        )
        check(
            "manual_recovery_key_is_idempotent",
            recover(settings, repaired, "manual-after-loss")["execution_id"]
            == repaired["execution_id"],
        )
        worker.terminate()
        worker.wait(timeout=10)
        worker = None

        # The following cases use real database/filesystem with SYNTHETIC stage
        # payloads and usage counters. They are not model/agent performance evidence.
        row = submit("synthetic-protocol")
        check("claim_once", store.claim(row) and not store.claim(row))
        operation = store.begin_call(row)
        store.settle_call(row, operation, {"input_tokens": 100, "output_tokens": 10})
        directory = execution_dir(settings, row)
        directory.mkdir(parents=True, exist_ok=True)
        bundle = directory / "bundle"
        (bundle / "workspace").mkdir(parents=True)
        (bundle / "workspace/file.txt").write_text("committed prefix")
        context = {
            "schema_version": 2,
            "stage": 1,
            "workspace_digest": tree_digest(bundle / "workspace"),
            "event_cursor": 0,
            "event_boundary": [],
            "ledger_revision": store.get(row["trial_id"])["ledger_revision"],
            "test_kind": "synthetic protocol boundary",
            "result": {"status": "checkpointed", "reason": "synthetic-stage"},
        }
        cp = store.checkpoint_prepare(row, context)
        rejected(
            "preparing_checkpoint_not_restorable", lambda: store.checkpoint(cp["checkpoint_id"])
        )
        check(
            "active_upload_gc_protected",
            cp["checkpoint_id"] not in checkpoint.collect(settings, 0)["deleted"],
        )
        ref = checkpoint.artifacts(settings).commit(
            bundle,
            ExecutionRef.from_dict(row),
            event_cursor=0,
            summary="synthetic",
            context=context,
            checkpoint_id=UUID(cp["checkpoint_id"]),
        )
        rejected(
            "uploaded_without_db_commit_not_restorable",
            lambda: store.checkpoint(cp["checkpoint_id"]),
        )
        cp = checkpoint.publish(settings, row, bundle, context)
        again = checkpoint.publish(settings, row, bundle, context)
        check(
            "lost_upload_and_commit_reply_idempotent",
            cp["checkpoint_id"] == again["checkpoint_id"]
            and len(
                [e for e in store.events(row["trial_id"]) if e["kind"] == "checkpoint.committed"]
            )
            == 1,
        )
        one = directory / "restore-one"
        two = directory / "restore-two"
        checkpoint.validate(settings, cp, one)
        checkpoint.validate(settings, cp, two)
        (one / "workspace/file.txt").write_text("branch mutation")
        check(
            "restored_directories_are_independent",
            (two / "workspace/file.txt").read_text() == "committed prefix",
        )
        file = (
            checkpoint.artifacts(settings).root / cp["checkpoint_id"] / "files/workspace/file.txt"
        )
        original = file.read_bytes()
        file.write_bytes(b"corrupt")
        rejected(
            "corrupt_checkpoint_rejected",
            lambda: checkpoint.validate(settings, cp, directory / "bad-restore"),
        )
        check(
            "corruption_never_publishes_partial_restore", not (directory / "bad-restore").exists()
        )
        file.write_bytes(original)

        # Concurrent requests cannot both advance the same observed epoch.
        def race(key):
            try:
                return store.advance(row, key, recovery=True)
            except Fenced:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, ["race-a", "race-b"]))
        check("concurrent_recovery_one_winner", sum(o is not None for o in outcomes) == 1)
        new = store.get(row["trial_id"])
        rejected("expired_epoch_cannot_renew_lease", lambda: store.heartbeat(row), Fenced)
        check(
            "recovery_preserves_prefix_usage",
            store.ledger(row["trial_id"])["known_usage"]["input_tokens"] == 100,
        )
        check("new_execution_claim", store.claim(new))
        operation = store.begin_call(new)
        third = store.advance(new, "second-loss", recovery=True)
        ledger = store.ledger(row["trial_id"])
        check(
            "unknown_usage_not_zeroed",
            ledger["unknown_operations"] == 1 and ledger["known_usage"]["input_tokens"] == 100,
        )
        store.settle_call(new, operation, {"input_tokens": 50, "output_tokens": 5})
        ledger = store.ledger(row["trial_id"])
        check(
            "late_usage_settles_original_operation_only",
            ledger["unknown_operations"] == 0
            and ledger["known_usage"]["input_tokens"] == 150
            and store.get(row["trial_id"])["epoch"] == 3,
        )
        check(
            "recovery_request_reply_loss_idempotent",
            store.advance(new, "second-loss", recovery=True)["execution_id"]
            == third["execution_id"],
        )
        store.cancel(row["trial_id"])
        store.finish(third, {"status": "failed", "reason": "test-end"}, controller=True)
        for e in store.executions(row["trial_id"]):
            store.cleaned(e["execution_id"])
        store.pin(cp["checkpoint_id"], "branch:future-m3")
        store.retire(row["trial_id"])
        check(
            "branch_reference_survives_trial_retirement",
            cp["checkpoint_id"] not in checkpoint.collect(settings, 0)["deleted"],
        )
        store.pin(cp["checkpoint_id"], "branch:future-m3", remove=True)

        def pin_race():
            try:
                store.pin(cp["checkpoint_id"], "branch:gc-race")
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            pin_future = pool.submit(pin_race)
            gc_future = pool.submit(checkpoint.collect, settings, 0)
            pinned, collected = pin_future.result(), gc_future.result()
        check(
            "gc_and_branch_reference_race_safe",
            not (pinned and cp["checkpoint_id"] in collected["deleted"]),
        )
        if pinned:
            store.pin(cp["checkpoint_id"], "branch:gc-race", remove=True)
        removed = checkpoint.collect(settings, 0)["deleted"] + collected["deleted"]
        check(
            "unreferenced_artifact_collected",
            cp["checkpoint_id"] in removed,
        )
        rejected("deleted_checkpoint_not_restorable", lambda: store.checkpoint(cp["checkpoint_id"]))
        check(
            "gc_is_repeatable",
            cp["checkpoint_id"] not in checkpoint.collect(settings, 0)["deleted"],
        )

        late = checkpoint.artifacts(settings).root / cp["checkpoint_id"]
        late.mkdir()
        (late / "late-upload").write_text("stale uploader after tombstone")
        check(
            "late_upload_after_tombstone_collected",
            cp["checkpoint_id"] in checkpoint.collect(settings, 0)["deleted"] and not late.exists(),
        )

        cancel = submit("cancel-commit-race")
        store.claim(cancel)
        call = store.begin_call(cancel)
        store.settle_call(cancel, call, None)
        context = {**context, "ledger_revision": store.get(cancel["trial_id"])["ledger_revision"]}
        pending = store.checkpoint_prepare(cancel, context)
        store.cancel(cancel["trial_id"])
        rejected(
            "cancel_blocks_checkpoint_commit",
            lambda: store.checkpoint_commit(
                cancel, pending["checkpoint_id"], ref.manifest_sha256, context
            ),
            Fenced,
        )
        store.finish(cancel, {"status": "failed", "reason": "cancelled"}, controller=True)
        check(
            "abandoned_upload_collected",
            pending["checkpoint_id"] in checkpoint.collect(settings, 0)["deleted"],
        )
        check(
            "real_trial_head_protected",
            source["checkpoint_id"] not in checkpoint.collect(settings, 0)["deleted"],
        )
        completed = True
    finally:
        if paused:
            os.kill(paused, signal.SIGCONT)
        for initial in rows:
            current = store.get(initial["trial_id"])
            if current["status"] not in {"succeeded", "failed", "cancelled"}:
                store.cancel(current["trial_id"])
                wait(
                    lambda initial=initial: (
                        reconcile(settings, initial["trial_id"], launch=False)["cleanup"]
                        == "complete"
                    )
                )
        if worker and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=10)
        report = {
            "status": "passed" if completed and all(checks.values()) else "failed",
            "mode": "real-services-plus-labeled-synthetic-protocol-faults",
            "model_calls": 0,
            "checks": checks,
            "elapsed_seconds": round(time.monotonic() - began, 2),
            "trials": [summary(store.get(r["trial_id"])) for r in rows],
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
