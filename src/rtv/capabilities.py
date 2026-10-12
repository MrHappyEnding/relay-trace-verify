"""Versioned compatibility statements, not general claims about every upstream adapter."""

from dataclasses import asdict

from rtv.codex_agent import CODEX_VERSION, DEFAULT_MODEL
from rtv.domain import AdapterCapabilities
from rtv.harbor_probe import HARBOR_VERSION


def capability_report() -> dict:
    return {
        "schema_version": 3,
        "scope": "M0–M3 local trusted tasks; real cross-agent development-task acceptance passed",
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
            "milestone": "M3 single-host protocol and real two-agent acceptance",
            "control": "Temporal 1.34.0 SDK + PostgreSQL product metadata",
            "metadata_schema": "rtv3 (M1/M2 metadata is retained, not migrated)",
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
            "ledger_scope": "stage calls plus mini API requests; not each internal agent tool",
            "strict_budget_admission": False,
            "artifact_gc": (
                "reference-aware local checkpoint GC; handoff pins protect active branches"
            ),
            "evidence": [
                "docs/evidence/m1-runtime-arm64-20261008.json",
                "docs/evidence/m1-codex-arm64-20261008.json",
                "docs/evidence/m2-runtime-arm64-20261009.json",
                "docs/evidence/m2-codex-arm64-20261009.json",
                "docs/evidence/m3-runtime-arm64-20261009.json",
                "docs/evidence/m3-real-arm64-20261009.json",
            ],
        },
        "cluster": {
            "milestone": "M4 phase one; formal experiments pending",
            "backend": "k3d 5.8.3 / Kubernetes 1.31.5 on one Docker Desktop host",
            "metadata_schema": "rtv4, isolated from rtv3",
            "admission": "PostgreSQL per-root jobs/CPU/memory reservations plus namespace quota",
            "cleanup": "UID-conditional deletion; retain reservation until Job and Pods absent",
            "recovery": "fresh Job/epoch/workspace from committed checkpoint, unchanged TTL",
            "model_calls_enabled": False,
            "security_scope": "trusted local fixtures; privileged outer DinD Pod",
            "storage": "shared local bind volume; no host-loss or storage-HA claim",
            "evidence": "docs/evidence/m4-runtime-arm64-20261011.json",
        },
        "mini": {
            "version": "2.4.6",
            "configured_model": "gpt-5.4-mini-2026-03-17",
            "real_model_verified": True,
            "verified_scope": (
                "upstream DefaultAgent with pinned real API model and Harbor tools; "
                "one trusted development task, three context policies"
            ),
            "native_session_import": False,
            "portable_context": "text projection, not native tool-history import",
            "transport": "host-side Responses API; credentials not mounted into sandbox",
            "request_limits": {"calls": 8, "input_bytes": 40000, "max_output_tokens": 4096},
            "budget": "non-reusable $0.062 reservation per request, $0.50 per branch; no retries",
            "strict_budget_admission": False,
            "budget_scope": "pinned transport reservation; not a provider invoice guarantee",
            "evidence": "docs/evidence/m3-real-arm64-20261009.json",
        },
        "benchmark": {
            "protocol": "configs/benchmarks/swebench-verified-v1.json",
            "selection": "4 development tasks; 20 evaluation tasks, 2 repeats",
            "analysis": "paired task-cluster bootstrap; missing and UNKNOWN retained",
            "verifier_control_tasks": ["sympy__sympy-22914"],
            "verifier_scope": "offline AMD64 Docker controls on an ARM64 host; no agents",
            "real_model_benchmark_verified": False,
            "kubernetes_benchmark_verified": False,
            "paid_experiment_authorized": False,
            "repository_runtime": {
                "scope": "one local SWE-bench pilot; scripted/oracle controls, "
                "no real-model result",
                "workspace": "/testbed",
                "checkpoint_manifest": 3,
                "directory_permission_bits": "0o777",
                "workspace_excludes": [".DS_Store"],
                "codex_native_repository_verified": False,
                "mini_scripted_repository_verified": True,
                "fresh_request_limit": 16,
            },
            "evidence": [
                "docs/evidence/m4-benchmark-preflight-arm64-20261011.json",
                "docs/evidence/m4-benchmark-protocol-20261011.json",
                "docs/evidence/m4-repository-runtime-arm64-20261011.json",
            ],
        },
        "relay": {
            "policies": ["native", "task-only", "summary", "portable"],
            "handoff": "same Trial, new Attempt; prepared context before atomic authority transfer",
            "fork": "independent Trials from one sealed checkpoint; referenced prefix operations",
            "verification": "fresh sandbox, declared workspace only, no network",
            "real_cross_agent_verified": True,
            "verified_scope": "Codex-to-mini workspace/summary/text projection on one fixed task",
            "evidence": [
                "docs/evidence/m3-runtime-arm64-20261009.json",
                "docs/evidence/m3-real-arm64-20261009.json",
            ],
        },
        "not_verified": [
            "mini native-session import",
            "cross-agent benchmark success rates or cost advantages",
            "multimodal ATIF",
            "mid-tool continuation",
            "host loss / cross-host takeover",
            "multi-host ownership and external side-effect fencing",
        ],
    }
