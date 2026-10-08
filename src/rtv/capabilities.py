"""Versioned compatibility statements, not general claims about every upstream adapter."""

from dataclasses import asdict

from rtv.codex_agent import CODEX_VERSION, DEFAULT_MODEL
from rtv.domain import AdapterCapabilities
from rtv.harbor_probe import HARBOR_VERSION


def capability_report() -> dict:
    return {
        "schema_version": 1,
        "scope": "M0 local trusted Linux filesystem tasks through the RTV runner",
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
                "single-process atomic rename; no host-power-loss or distributed guarantees"
            ),
        },
        "not_verified": [
            "other agents",
            "cross-model / cross-framework ATIF",
            "multimodal ATIF",
            "mid-tool continuation",
            "worker crash recovery",
            "distributed fencing",
        ],
    }
