"""Versioned task contract; source prompts never include verifier or oracle files."""

import json
import math
import re
from pathlib import Path

from rtv.benchmark.protocol import digest
from rtv.harbor_probe import tree_digest
from rtv.runtime.config import task_path


def validate_contract(value):
    body = {k: v for k, v in value.items() if k != "contract_sha256"}
    if value.get("schema_version") != 1 or value.get("contract_sha256") != digest(body):
        raise ValueError("Task contract changed or has an unsupported version")
    if value.get("kind") != "swebench" or value.get("workspace") != "/testbed":
        raise ValueError("Unsupported real-repository workspace contract")
    if value.get("platform") != "linux/amd64" or value.get("split") not in {"pilot", "evaluation"}:
        raise ValueError("Only pinned AMD64 benchmark tasks are supported")
    if value.get("workspace_excludes") != [".DS_Store"]:
        raise ValueError("Repository workspace exclusions must be explicit")
    if not Path(value["task_directory"]).is_absolute():
        raise ValueError("Task directory must be absolute")
    for key in ("agent_image_id", "verifier_image_id"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]):
            raise ValueError("Task images must use immutable local image IDs")
    for key in ("protocol_sha256", "task_digest"):
        if not re.fullmatch(r"[0-9a-f]{64}", value[key]):
            raise ValueError("Invalid task provenance digest")
    if not re.fullmatch(r"[0-9a-f]{40}", value["base_commit"]):
        raise ValueError("Task base commit must be pinned")
    if (
        type(value["verifier_timeout_seconds"]) is not int
        or not 30 <= value["verifier_timeout_seconds"] <= 1800
    ):
        raise ValueError("Verifier timeout must be 30–1800 seconds")
    return value


def contract(spec):
    value = spec.get("task_contract")
    if value is not None:
        validate_contract(value)
        if (
            spec["task_digest"] != value["task_digest"]
            or spec["image_id"] != value["agent_image_id"]
        ):
            raise ValueError("Execution spec disagrees with task contract")
    return value


def directory(spec):
    value = contract(spec)
    return (
        Path(value["task_directory"])
        if value
        else task_path(spec.get("task_name", spec["adapter"]))
    )


def workspace(spec):
    value = contract(spec)
    return value["workspace"] if value else "/workspace"


def workspace_digest(path, spec):
    declared = contract(spec)
    return tree_digest(path, exclude_names=declared["workspace_excludes"] if declared else ())


def prompt(spec, partial):
    value = contract(spec)
    if value:
        task = directory(spec)
        if tree_digest(task) != value["task_digest"]:
            raise ValueError("Task definition changed after freezing")
        instruction = (task / "instruction.md").read_text()
        if len(instruction.encode()) > 64000:
            raise ValueError("Task prompt exceeds the supported envelope; no silent truncation")
        return (
            "Resolve the repository issue in /testbed. Preserve existing work and run relevant "
            "public tests. Do not read /tests, /logs or credentials, use network tools, or spawn "
            "other agents. Finish one bounded turn with a concise report of changes, tests and "
            "remaining work. Do not introduce a deliberate defect.\n\n" + instruction
        )
    text = (
        "Work only in /workspace. Modify positive_sum.py to sum only positive values, "
        "but deliberately return None for an empty list. Keep that staged defect, "
        "check it, then stop."
        if partial
        else "Work only in /workspace. Finish positive_sum.py: sum positive values, "
        "return zero for empty inputs including generators. Check the solution, then stop."
    )
    return text + " Do not read /logs or credentials, use network tools or spawn other agents."


def scripted_commands(spec, partial=False):
    """Explicit developer controls, never classified as model outcomes."""
    if not contract(spec):
        return None
    commands = spec.get("scripted_source" if partial else "scripted_target")
    if (
        not isinstance(commands, list)
        or not 1 <= len(commands) <= 16
        or any(not isinstance(c, str) or not c.strip() or len(c.encode()) > 64000 for c in commands)
    ):
        raise ValueError("Real-repository fixtures require explicit bounded scripted controls")
    return commands


def prepare_contract(plan, upstream, instance_id, preflight, agent_image=None, *, split="pilot"):
    from rtv.benchmark.protocol import environment_protocol, load
    from rtv.benchmark.source import audit, catalog_for
    from rtv.harbor_probe import _docker

    protocol = load(plan)
    audit(protocol, catalog_for(plan), upstream)
    selected = next(t for t in protocol["tasks"] if t["instance_id"] == instance_id)
    report = json.loads(Path(preflight).read_text())
    if (
        selected["split"] != split
        or split not in {"pilot", "evaluation"}
        or report.get("status") != "passed"
        or report.get("task") != instance_id
        or report.get("protocol_sha256") != environment_protocol(protocol)
        or report.get("base_image") != selected["image"]["pinned"]
        or not all(
            report.get("controls", {}).get(k, {}).get("passed") for k in ("unmodified", "oracle")
        )
    ):
        raise ValueError("A matching passed task preflight in the requested split is required")
    verifier = report["verifier_image_id"]
    image = agent_image or verifier
    images = []
    for ref in (image, verifier):
        info = json.loads(_docker("image", "inspect", ref))[0]
        if info["Architecture"] != "amd64" or info["Os"] != "linux":
            raise ValueError("Image does not match the pinned task platform")
        images.append(info["Id"])
    task = (upstream / selected["harbor_path"]).resolve()
    body = {
        "schema_version": 1,
        "kind": "swebench",
        "instance_id": instance_id,
        "split": split,
        "protocol_sha256": environment_protocol(protocol),
        "task_directory": str(task),
        "task_digest": tree_digest(task),
        "workspace": "/testbed",
        "workspace_excludes": [".DS_Store"],
        "platform": selected["image"]["platform"],
        "base_commit": selected["base_commit"],
        "agent_image_id": images[0],
        "verifier_image_id": images[1],
        "verifier_timeout_seconds": 600,
    }
    return validate_contract({**body, "contract_sha256": digest(body)})


def make_repository_spec(args):
    from rtv.codex_agent import DEFAULT_MODEL
    from rtv.mini_agent import MINI_MODEL

    declared = validate_contract(json.loads(args.task_contract.read_text()))
    if args.adapter not in {"fixture", "mini-fixture", "codex", "mini"}:
        raise ValueError("Unsupported repository adapter")
    if not 30 <= args.ttl <= 1800 or not 1 <= args.agent_seconds <= 600 or args.delay:
        raise ValueError("Repository runs need TTL 30–1800, agent limit 1–600 and no fixture delay")
    if args.pause_after_checkpoint and not args.staged:
        raise ValueError("Pausing requires --staged")
    if args.staged and args.adapter not in {"fixture", "codex"}:
        raise ValueError("Repository prefix must use fixture or Codex")
    if args.image:
        raise ValueError("Repository images must be selected in the frozen task contract")
    if not 0 <= args.max_recoveries <= 3:
        raise ValueError("Recovery allowance must be 0–3")
    if args.adapter == "codex" and not args.allow_model_calls:
        raise ValueError("Codex requires --allow-model-calls")
    if args.adapter == "mini" and not args.allow_api_calls:
        raise ValueError("Mini requires --allow-api-calls")
    if (
        not 1 <= args.api_call_limit <= 16
        or not math.isfinite(args.api_budget_usd)
        or not 0 <= args.api_budget_usd <= 8
    ):
        raise ValueError("Invalid repository API admission limits")
    if args.adapter == "mini" and (
        args.model not in {None, MINI_MODEL} or args.api_budget_usd <= 0
    ):
        raise ValueError("Mini requires the pinned model and a positive approved admission budget")
    controls = {}
    if args.adapter in {"fixture", "mini-fixture"}:
        if args.scripted_controls is None:
            raise ValueError("Repository fixture requires --scripted-controls")
        controls = json.loads(args.scripted_controls.read_text())
        if set(controls) != {"scripted_source", "scripted_target"}:
            raise ValueError("Unsupported scripted control fields")
    elif args.scripted_controls is not None:
        raise ValueError("Real adapters cannot receive scripted reference controls")
    spec = {
        "adapter": args.adapter,
        "model": (args.model or DEFAULT_MODEL)
        if args.adapter == "codex"
        else MINI_MODEL
        if args.adapter == "mini"
        else None,
        "ttl_seconds": args.ttl,
        "agent_seconds": args.agent_seconds,
        "delay_seconds": 0,
        "image_id": declared["agent_image_id"],
        "task_digest": declared["task_digest"],
        "task_name": declared["instance_id"],
        "task_contract": declared,
        "stages": 2 if args.staged else 1,
        "pause_after_checkpoint": args.pause_after_checkpoint,
        "isolated_verifier": True,
        "max_recoveries": args.max_recoveries,
        "api_call_limit": args.api_call_limit,
        "api_budget_usd": args.api_budget_usd,
        **controls,
    }
    if tree_digest(directory(spec)) != spec["task_digest"]:
        raise ValueError("Task definition changed before submission")
    if controls:
        scripted_commands(spec, partial=True)
        scripted_commands(spec)
    return spec
