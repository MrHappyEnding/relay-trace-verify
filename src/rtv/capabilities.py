"""Versioned compatibility statements, not general claims about every upstream adapter."""

from dataclasses import asdict

from rtv.codex_agent import CODEX_VERSION, DEFAULT_MODEL
from rtv.domain import AdapterCapabilities
from rtv.harbor_probe import HARBOR_VERSION


def capability_report() -> dict:
    return {
        "schema_version": 2,
        "scope": "M0–M2 local trusted Linux filesystem tasks through RTV",
        "codex": {
            "cli_version": CODEX_VERSION,
            "harbor_version": HARBOR_VERSION,
            "tested_model": DEFAULT_MODEL,
            "tested_arch": "arm64",
            "capabilities": asdict(
                AdapterCapabilities(
                    stage_checkpoint=True,
                    native_session_import=True,
                    portable_trajectory_import=True,
                    strict_budget_admission=False,
                )
            ),
            "native_scope": "same CLI/model/image, completed stage, /workspace",
            "atif_scope": "same pinned Codex/model; complete text messages and paired tool results",
            "interruption_scope": (
                "stop whole container, discard incomplete stage, restore last commit"
            ),
            "budget_scope": (
                "wall-clock termination and observed usage; interrupted usage is UNKNOWN"
            ),
            "evidence": [
                "docs/evidence/m0-codex-arm64-20261008.json",
                "docs/evidence/m0-boundaries-arm64-20261008.json",
            ],
        },
        "checkpoint_policy": {
            "include": ["/workspace", "one completed native rollout", "session/version metadata"],
            "generated_files": "include regular files required by the task under /workspace",
            "quiescence": "confirm container stopped before export; no external workspace writers",
            "preserve": [
                "relative paths",
                "regular file bytes",
                "regular-file permission bits 0o777",
                "empty directories",
            ],
            "reject": [
                "symbolic links",
                "special files",
                "path traversal",
                "content hash mismatch",
            ],
            "exclude": [
                "credentials",
                "process memory",
                "external services",
                "UID/GID",
                "ACLs",
                "xattrs",
                "directory permission bits (restored with process defaults)",
            ],
            "durability": (
                "local fsync + atomic rename; runtime v2 additionally requires DB commit; "
                "host power loss and cross-host recovery not verified"
            ),
        },
        "runtime": {
            "milestone": "M2 single-host trusted fixtures",
            "control": "Temporal 1.34.0 SDK + PostgreSQL product metadata",
            "metadata_schema": "rtv2 (M1 metadata is retained, not migrated)",
            "worker_restart": "take over observation of a surviving local supervisor/sandbox",
            "supervisor_loss": (
                "new execution from committed completed stage only with explicit recovery "
                "allowance; pending usage remains UNKNOWN"
            ),
            "automatic_checkpoint_recovery": True,
            "recovery_scope": (
                "supervisor loss or lease expiry; default allowance 0, maximum 3; "
                "container/adapter failures require manual recovery; TTL never resets"
            ),
            "metadata_epoch_fencing": True,
            "distributed_fencing": False,
            "ledger_scope": "one operation per physical stage call; not each internal agent tool",
            "strict_budget_admission": False,
            "artifact_gc": (
                "reference-aware local checkpoint GC; explicit pins protect future branches"
            ),
            "evidence": [
                "docs/evidence/m1-runtime-arm64-20261008.json",
                "docs/evidence/m1-codex-arm64-20261008.json",
                "docs/evidence/m2-runtime-arm64-20261009.json",
                "docs/evidence/m2-codex-arm64-20261009.json",
            ],
        },
        "not_verified": [
            "other agents",
            "cross-model / cross-framework ATIF",
            "multimodal ATIF",
            "mid-tool continuation",
            "host loss / cross-host takeover",
            "multi-host ownership and external side-effect fencing",
        ],
    }
