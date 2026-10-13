"""Run-scoped credential references. Secret values never enter experiment metadata.

This is a trusted local cluster boundary, not isolation from namespace admins or
the privileged DinD executor. Kubernetes Secret names alone are not identities.
"""

import base64
import json
import math
import os
import re
import stat
from pathlib import Path

from rtv.codex_agent import DEFAULT_MODEL, validate_auth
from rtv.mini_agent import MINI_MODEL, api_envelope

MOUNT = "/run/rtv-model"
LABEL = "rtv.dev/credential-run"
ENV_KEYS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "CODEX_AUTH_JSON_PATH",
    "CODEX_FORCE_AUTH_JSON",
    "RTV_ALLOW_MODEL_CALLS",
    "RTV_ALLOW_API_CALLS",
    "RTV_CODEX_AUTH_FILE",
    "RTV_API_KEY_FILE",
}


def authorize(run, approved_budget_usd, references):
    """Called only by the explicit, owner-approved launcher, after plan validation."""
    from rtv.runtime.store import spec_hash

    if run["mode"] != "real-model":
        raise ValueError("Credential admission requires a real-model plan")
    grants = {}
    for entry in [run["source"], *run["entries"]]:
        spec = entry["spec"]
        validate_model_spec(spec)
        grants[entry["trial_id"]] = {
            "spec_hash": spec_hash(spec),
            "adapter": spec["adapter"],
            "requests": spec["api_call_limit"] if spec["adapter"] == "mini" else 0,
        }
        if "api_envelope" in spec:
            grants[entry["trial_id"]]["api_envelope"] = spec["api_envelope"]
    access = {
        "schema_version": 1,
        "run_sha256": run["run_sha256"],
        "root": run["root"],
        "approved_budget_usd": approved_budget_usd,
        "grants": grants,
        "secrets": references,
    }
    validate_access(access)
    return access


def authorize_recovery(run, references):
    """Two Codex trials, one explicitly bounded recovery, and no metered API access."""
    from rtv.cluster.recovery import validate_run
    from rtv.runtime.store import spec_hash

    validate_run(run)
    access = {
        "schema_version": 1,
        "purpose": "codex-recovery-v1",
        "run_sha256": run["run_sha256"],
        "root": run["root"],
        "approved_budget_usd": 0,
        "grants": {
            entry["trial_id"]: {
                "spec_hash": spec_hash(entry["spec"]),
                "adapter": "codex",
                "requests": 0,
                "max_recoveries": entry["spec"]["max_recoveries"],
            }
            for entry in [run["source"], *run["entries"]]
        },
        "secrets": references,
    }
    validate_access(access)
    return access


def validate_model_spec(spec, *, allow_recovery=False):
    envelope = api_envelope(spec)
    adapter = spec["adapter"]
    if "prefix_policy" in spec:
        from rtv.codex_gate import policy

        policy(spec["prefix_policy"])
        if adapter != "codex" or spec.get("stages") != 2 or not spec.get("pause_after_checkpoint"):
            raise ValueError("Tool gate is only admitted for a sealed Codex source prefix")
    if adapter not in {"codex", "mini"} or spec.get("model") != {
        "codex": DEFAULT_MODEL,
        "mini": MINI_MODEL,
    }.get(adapter):
        raise ValueError("Cluster model run requires the pinned adapters and models")
    if (
        not spec.get("task_contract")
        or type(spec.get("max_recoveries")) is not int
        or spec["max_recoveries"] not in ((0, 1) if allow_recovery and adapter == "codex" else (0,))
        or not spec.get("isolated_verifier")
        or any(k.startswith("scripted_") for k in spec)
        or spec.get("ttl_seconds") != 1800
        or type(spec.get("agent_seconds")) is not int
        or not 1 <= spec["agent_seconds"] <= 600
    ):
        raise ValueError("Model run requires a bounded repository spec without replay or controls")
    if adapter == "mini":
        calls, budget = spec.get("api_call_limit"), spec.get("api_budget_usd")
        if (
            type(calls) is not int
            or not 1 <= calls <= 16
            or budget != round(calls * envelope.reservation_usd, 9)
        ):
            raise ValueError("API envelope must match the non-reusable request reservations")


def validate_access(access):
    if (
        access.get("schema_version") != 1
        or not re.fullmatch(r"[0-9a-f]{64}", access.get("run_sha256", ""))
        or not Path(access.get("root", "")).is_absolute()
    ):
        raise ValueError("Invalid run-scoped model admission")
    purpose = access.get("purpose", "paired-benchmark-v1")
    if purpose not in {"paired-benchmark-v1", "codex-recovery-v1"}:
        raise ValueError("Unknown model admission purpose")
    recovery = purpose == "codex-recovery-v1"
    budget = access.get("approved_budget_usd")
    if (
        type(budget) not in (int, float)
        or not math.isfinite(budget)
        or (budget != 0 if recovery else not 0 < budget <= 8)
    ):
        raise ValueError("One pilot approval must be finite and at most $8")
    grants = access.get("grants", {})
    adapters = ["codex", "codex"] if recovery else ["codex", "codex", "mini", "mini", "mini"]
    if (
        len(grants) != len(adapters)
        or sorted(g.get("adapter", "") for g in grants.values()) != adapters
    ):
        raise ValueError("Admission must cover exactly the prefix and four pilot branches")
    if recovery and sorted(g.get("max_recoveries", -1) for g in grants.values()) != [0, 1]:
        raise ValueError("Recovery admission permits one source and one single-recovery trial")
    for trial, grant in grants.items():
        api_envelope(grant)
        recoveries = grant.get("max_recoveries", 0)
        if type(recoveries) is not int or recoveries not in ((0, 1) if recovery else (0,)):
            raise ValueError("Unapproved recovery allowance")
        if not re.fullmatch(r"[0-9a-f-]{36}", trial) or not re.fullmatch(
            r"[0-9a-f]{64}", grant.get("spec_hash", "")
        ):
            raise ValueError("Invalid trial identity or spec hash")
        n = grant.get("requests")
        if type(n) is not int or (n not in {8, 16} if grant["adapter"] == "mini" else n != 0):
            raise ValueError("Invalid pilot request allowance")
    total = sum(g["requests"] for g in grants.values())
    reserved = sum(g["requests"] * api_envelope(g).reservation_usd for g in grants.values())
    if total != (0 if recovery else 32) or round(reserved, 9) > budget:
        raise ValueError("Pilot reservations exceed approval or differ from the frozen protocol")
    if set(access.get("secrets", {})) != ({"codex"} if recovery else {"codex", "mini"}):
        raise ValueError("Separate Codex and mini Secret references required")
    for ref in access["secrets"].values():
        if (
            set(ref) != {"name", "uid"}
            or not re.fullmatch(r"rtv-model-[a-z0-9-]{1,50}", ref["name"])
            or not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", ref["uid"])
        ):
            raise ValueError("Invalid immutable Secret reference")


def admitted_reference(pool, root, row):
    spec = row.get("spec", {})
    if spec.get("adapter") not in {"codex", "mini"}:
        return None
    from rtv.runtime.store import spec_hash

    access = pool.get("model_access")
    if not access:
        raise ValueError("Cluster model credential delivery is not enabled")
    validate_access(access)
    recovery = access.get("purpose") == "codex-recovery-v1"
    validate_model_spec(spec, allow_recovery=recovery)
    grant = access["grants"].get(row["trial_id"])
    epoch, recoveries = row.get("epoch"), row.get("recoveries", 0)
    allowed = grant.get("max_recoveries", 0) if grant else 0
    valid_epoch = (
        type(epoch) is int
        and type(recoveries) is int
        and 0 <= recoveries <= allowed
        and epoch == 1 + recoveries
        and (
            epoch == 1
            or (
                recovery
                and bool(row.get("parent_checkpoint"))
                and row.get("resume_from") == row["parent_checkpoint"]
            )
        )
    )
    if (
        str(root) != access["root"]
        or not valid_epoch
        or not grant
        or grant["spec_hash"] != spec_hash(spec)
        or grant["adapter"] != spec["adapter"]
        or allowed != spec["max_recoveries"]
        or grant["requests"] != (spec["api_call_limit"] if spec["adapter"] == "mini" else 0)
        or api_envelope(grant) != api_envelope(spec)
    ):
        raise ValueError("Execution is outside the approved identities, specs or first epoch")
    return access["secrets"][spec["adapter"]]


def mount_credential(pod, reference):
    if reference is None:
        return
    pod["volumes"].append(
        {
            "name": "model-credential",
            "secret": {
                "secretName": reference["name"],
                "defaultMode": 0o400,
                "items": [{"key": "credential", "path": "credential"}],
            },
        }
    )
    pod["containers"][0]["volumeMounts"].append(
        {
            "name": "model-credential",
            "mountPath": MOUNT,
            "readOnly": True,
        }
    )


def supervisor_environment(pool, root, row):
    """Clear inherited auth, then opt in only the admitted adapter using a file path."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    if admitted_reference(pool, root, row):
        path = Path(MOUNT) / "credential"
        # Projected Secret files are symlinks managed by kubelet; validate the target.
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 0 < info.st_size <= 262144:
            raise ValueError("Missing or unsafe projected credential")
        if row["spec"]["adapter"] == "codex":
            validate_auth(path)
            env.update(RTV_ALLOW_MODEL_CALLS="1", RTV_CODEX_AUTH_FILE=str(path))
        else:
            if not path.read_text().strip():
                raise ValueError("Empty projected API credential")
            env.update(RTV_ALLOW_API_CALLS="1", RTV_API_KEY_FILE=str(path))
    return env


def install_secret(kube, run_sha, adapter, path):
    """Direct API create, never kubectl argv/stdin dumps or shared-volume staging."""
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or not 0 < info.st_size <= 262144
        or path.resolve().is_relative_to(Path(kube.config.shared_path).resolve())
    ):
        raise ValueError("Use a private regular credential file outside the cluster shared volume")
    if adapter == "codex":
        validate_auth(path)
    elif adapter != "mini":
        raise ValueError("Unknown credential adapter")
    value = path.read_text().strip()
    if not value:
        raise ValueError("Empty credential")
    name = f"rtv-model-{run_sha[:24]}-{adapter}"
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "immutable": True,
        "metadata": {"name": name, "labels": {LABEL: run_sha[:63]}},
        "stringData": {"credential": value},
    }
    try:
        result = kube.core.create_namespaced_secret(
            kube.config.namespace, body, _request_timeout=(3, 10), _preload_content=False
        )
        meta = json.loads(result.data)["metadata"]
    except Exception:
        # Recover a lost create acknowledgement without creating/updating a second
        # Secret. Compare in memory, never return the contents or a provider error.
        try:
            found = kube.core.read_namespaced_secret(
                name, kube.config.namespace, _request_timeout=(3, 10), _preload_content=False
            )
            existing = json.loads(found.data)
            if (
                existing.get("immutable") is not True
                or existing["metadata"].get("labels", {}).get(LABEL) != run_sha[:63]
                or existing.get("data") != {"credential": base64.b64encode(value.encode()).decode()}
            ):
                raise ValueError("Secret differs")
            meta = existing["metadata"]
        except Exception:
            raise RuntimeError(
                "Secret create outcome unresolved; inspect owned Secret metadata before retrying"
            ) from None
    return {"name": meta["name"], "uid": meta["uid"]}


def check_secret(kube, access, ref):
    try:
        response = kube.core.read_namespaced_secret(
            ref["name"], kube.config.namespace, _request_timeout=(3, 10), _preload_content=False
        )
        data = json.loads(response.data)
    except Exception:
        raise ValueError("Credential Secret unavailable; no Job admitted") from None
    meta = data["metadata"]
    if (
        meta["uid"] != ref["uid"]
        or not data.get("immutable")
        or meta.get("labels", {}).get(LABEL) != access["run_sha256"][:63]
        or set(data.get("data", {})) != {"credential"}
    ):
        raise ValueError("Credential Secret identity, ownership or shape changed")


def remove_secret(kube, access, ref):
    check_secret(kube, access, ref)
    try:
        kube.core.delete_namespaced_secret(
            ref["name"],
            kube.config.namespace,
            body={"preconditions": {"uid": ref["uid"]}},
            _request_timeout=(3, 10),
        )
    except Exception:
        raise RuntimeError("Credential Secret cleanup was not acknowledged") from None
