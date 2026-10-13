"""Recheck completed recovery artifacts without a model, database, or cluster mutation."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from rtv.benchmark.protocol import digest
from rtv.cluster.recovery import validate_run
from rtv.runtime.checkpoint import validate
from rtv.runtime.config import Settings


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root, output):
    if output.exists():
        raise ValueError("Audit output already exists")
    prepared = json.loads((root / "prepared.json").read_text())
    body = {k: v for k, v in prepared.items() if k != "prepared_sha256"}
    if digest(body) != prepared["prepared_sha256"]:
        raise ValueError("Prepared run changed")
    run = prepared["run"]
    validate_run(run)
    original = json.loads((root / "report.json").read_text())
    raw = root / "runtime-report"
    report = json.loads((raw / "report.json").read_text())
    if original["run_sha256"] != run["run_sha256"]:
        raise ValueError("Evidence belongs to another run")
    for name, sha in report["sha256"].items():
        path = (raw / name).resolve()
        if not path.is_relative_to(raw.resolve()) or checksum(path) != sha:
            raise ValueError("Runtime evidence changed")
    source = json.loads((raw / "trials" / (run["source"]["trial_id"] + ".json")).read_text())
    target = json.loads((raw / "trials" / (run["entries"][0]["trial_id"] + ".json")).read_text())
    fault = original["fault"]
    manifest_path = root / "checkpoints" / fault["checkpoint"] / "manifest.json"
    if checksum(manifest_path) != fault["checkpoint_manifest"]:
        raise ValueError("Committed checkpoint manifest changed")
    manifest = json.loads(manifest_path.read_text())
    cp = {
        "checkpoint_id": fault["checkpoint"],
        "manifest_sha256": fault["checkpoint_manifest"],
        "context": manifest["context"],
        "execution_id": manifest["source"]["execution_id"],
        "epoch": manifest["source"]["epoch"],
    }
    with TemporaryDirectory(prefix="rtv-recovery-audit-") as temporary:
        validate(Settings(root, "", "", schema="rtv4"), cp, Path(temporary) / "restore")
    source_bundle = root / run["source"]["trial_id"] / "executions" / cp["execution_id"] / "bundle"
    changed_export_files = [
        e["path"]
        for e in manifest["files"]
        if e["path"].startswith("workspace/")
        and (
            not (source_bundle / e["path"]).is_file()
            or checksum(source_bundle / e["path"]) != e["sha256"]
        )
    ]
    index_refresh = changed_export_files == ["workspace/.git/index"]
    executions = target["executions"]
    if len(executions) != 2:
        raise ValueError("Expected exactly the interrupted execution and one recovery")
    old, new = executions
    events = target["events"]
    restores = [e for e in events if e["kind"] == "workspace.restored"]
    recovered = [e for e in events if e["kind"] == "execution.recovered"]
    allocations = {a["execution_id"]: a for a in report["pool"]}
    ledgers = [source["ledger"], target["ledger"]]
    operations = [o for ledger in ledgers for o in ledger["operations"]]
    checks = {
        "runtime_records_match_saved_hashes": True,
        "immutable_checkpoint_validates_all_files_and_modes": True,
        "export_changes_explained": not changed_export_files or index_refresh,
        "source_native_session_and_code_changes": bool(
            cp["context"]["result"].get("session_id") and fault["changed_files"]
        ),
        "source_sealed_without_hidden_verifier": (
            cp["context"]["result"]["status"] == "sealed"
            and cp["context"]["result"]["verification"]["status"] == "not_run"
        ),
        "native_process_observed_before_injection": original["checks"].get(
            "native_codex_process_observed_before_fault"
        )
        is True,
        "one_recovery_two_executions": (
            len(recovered) == 1
            and old["epoch"] == 1
            and new["epoch"] == 2
            and new["recoveries"] == 1
        ),
        "attempt_identity_and_deadline_preserved": (
            old["attempt_id"] == new["attempt_id"] and old["deadline"] == new["deadline"]
        ),
        "both_executions_restore_same_checkpoint": (
            len(restores) == 2
            and {e["payload"]["execution_id"] for e in restores}
            == {old["execution_id"], new["execution_id"]}
            and all(
                e["payload"]["checkpoint_id"] == cp["checkpoint_id"]
                and e["payload"]["workspace_digest"] == cp["context"]["workspace_digest"]
                for e in restores
            )
        ),
        "recovery_runs_on_another_node": (
            bool(allocations[new["execution_id"]]["node_name"])
            and allocations[old["execution_id"]]["node_name"]
            != allocations[new["execution_id"]]["node_name"]
        ),
        "old_execution_publication_rejected": original["checks"].get("old_execution_cannot_publish")
        is True,
        "native_session_preserved": target["result"].get("session_id")
        == cp["context"]["result"]["session_id"],
        "independent_verifier_completed": target["result"].get("verification", {}).get("status")
        == "completed"
        and target["result"]["verification"].get("isolated") is True,
        "interrupted_operation_remains_unknown": target["ledger"]["unknown_operations"] == 1,
        "bounded_subscription_no_api": len(operations) == 3
        and all(o["kind"] == "codex" for o in operations),
        "allocations_released": all(a["state"] == "released" for a in report["pool"]),
        "job_cleanup_recorded": original["checks"].get("all_owned_jobs_removed") is True,
        "credential_cleanup_recorded": original["checks"].get("owned_credentials_removed") is True,
    }
    final_restore = next(e for e in restores if e["payload"]["execution_id"] == new["execution_id"])
    # Keep the original harness result and its distinct export-directory diagnostic visible.
    result = {
        "schema_version": 1,
        "status": "passed" if all(checks.values()) else "failed",
        "mode": "offline-audit-of-real-model-recovery",
        "run_sha256": run["run_sha256"],
        "checks": checks,
        "original_harness_status": original["status"],
        "original_harness_checks": original["checks"],
        "changed_export_files": changed_export_files,
        "diagnostic": (
            "The original inspector refreshed the temporary export's Git index. "
            "The committed checkpoint is separately revalidated byte-for-byte, including "
            "restored permissions. Original records are retained; no model or verifier rerun."
        )
        if index_refresh
        else "Source export content matches the committed checkpoint.",
        "task": run["source"]["spec"]["task_contract"]["instance_id"],
        "verifier_reward": target["result"].get("rewards", {}).get("reward"),
        "native_session_preserved": checks["native_session_preserved"],
        "fault": {
            **fault,
            "new_execution": new["execution_id"],
            "new_node": allocations[new["execution_id"]]["node_name"],
            "restored_at": final_restore["at"],
            "fault_to_restore_seconds": (
                datetime.fromisoformat(final_restore["at"]) - datetime.fromisoformat(fault["at"])
            ).total_seconds(),
        },
        "api_requests": 0,
        "additional_model_calls": 0,
        "additional_verifier_calls": 0,
        "subscription_stages_in_audited_run": len(operations),
        "original_report_sha256": checksum(root / "report.json"),
        "runtime_report_sha256": checksum(raw / "report.json"),
        "raw_trial_sha256": report["sha256"],
        "checkpoint_manifest_sha256": checksum(manifest_path),
        "audit_script_sha256": checksum(Path(__file__)),
        "executed_source_sha256": prepared["source_sha256"],
        "runner_image": original["runner_image"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {"status": result["status"], "checks": checks, "reward": result["verifier_reward"]}
        )
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raise SystemExit(audit(args.root.resolve(), args.output.resolve()))
