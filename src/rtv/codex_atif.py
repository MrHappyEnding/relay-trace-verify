"""Pinned Harbor ATIF bridge with an explicit, deliberately narrow compatibility contract."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from rtv.codex_agent import CODEX_VERSION, read_session


def atif_codex_class():
    from harbor.agents.installed.codex import Codex

    class WorkspaceCodex(Codex):
        def atif_to_native_trajectory(self, trajectory, session_id):
            filename, content = super().atif_to_native_trajectory(trajectory, session_id)
            lines = content.splitlines()
            meta = json.loads(lines[0])
            # Harbor 0.24.0 hardcodes /app and uses the source agent version.
            # These describe the receiving runtime, not portable conversation content.
            meta["payload"].update(cwd="/workspace", cli_version=CODEX_VERSION)
            lines[0] = json.dumps(meta)
            return filename, "\n".join(lines) + "\n"

    return WorkspaceCodex


def validate_atif(path: Path, *, model: str) -> Any:
    from harbor.models.trajectories import Trajectory

    trajectory = Trajectory.model_validate_json(path.read_text())
    if (trajectory.agent.name, trajectory.agent.version, trajectory.agent.model_name) != (
        "codex",
        CODEX_VERSION,
        model,
    ):
        raise ValueError("Only pinned same-model Codex ATIF is verified by this adapter")
    UUID(trajectory.session_id or "")
    if trajectory.subagent_trajectories or trajectory.continued_trajectory_ref:
        raise ValueError("Nested or segmented ATIF is outside the M0 contract")
    if not any(step.source == "user" for step in trajectory.steps):
        raise ValueError("A resumable ATIF session needs a user message")
    seen: set[str] = set()
    for step in trajectory.steps:
        if step.message is not None and not isinstance(step.message, str):
            raise ValueError("Only text ATIF messages are supported")
        calls = [call.tool_call_id for call in step.tool_calls or []]
        results = step.observation.results if step.observation else []
        if len(set(calls)) != len(calls) or seen.intersection(calls):
            raise ValueError("Duplicate tool call identity")
        seen.update(calls)
        if Counter(calls) != Counter(result.source_call_id for result in results):
            raise ValueError("Every recorded tool call needs exactly one matching result")
        if any(not isinstance(result.content, str) for result in results):
            raise ValueError("Only text tool results are supported")
    return trajectory


def _items(events: list[dict[str, Any]], kind: str) -> Counter:
    """Compare supported content without writing raw prompts/tool output to reports."""
    items = []
    for event in events:
        if event.get("type") != "response_item":
            continue
        payload = event["payload"]
        item_type = payload.get("type", "")
        if kind == "messages" and item_type == "message":
            # Channels are audited separately; portable ATIF does not preserve them.
            value = [
                payload.get("role"),
                "".join(block.get("text", "") for block in payload.get("content", [])),
            ]
        elif kind == "calls" and item_type in {"function_call", "custom_tool_call"}:
            arguments = payload.get("arguments", payload.get("input"))
            if item_type == "function_call" and isinstance(arguments, str):
                arguments = json.loads(arguments)
            value = [item_type, payload.get("call_id"), payload.get("name"), arguments]
        elif kind == "results" and item_type in {"function_call_output", "custom_tool_call_output"}:
            output = payload.get("output")
            if isinstance(output, list):
                output = "\n".join(block.get("text", "") for block in output)
            value = [item_type, payload.get("call_id"), output]
        else:
            continue
        items.append(hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())
    return Counter(items)


def conversion_audit(source: Path, converted: str, trajectory: Any) -> dict[str, Any]:
    original = [json.loads(line) for line in source.read_text().splitlines()]
    rebuilt = [json.loads(line) for line in converted.splitlines()]
    preserved = {}
    for kind in ("messages", "calls", "results"):
        before, after = _items(original, kind), _items(rebuilt, kind)
        preserved[kind] = {
            "source": before.total(),
            "converted": after.total(),
            "retained_normalized": (before & after).total(),
            "lost_or_changed": (before - after).total(),
            "added_or_changed": (after - before).total(),
        }
    return {
        "schema": trajectory.schema_version,
        "steps": len(trajectory.steps),
        "content": preserved,
        "comparison": (
            "Message text blocks concatenate; tool text blocks join with newlines; "
            "structured arguments compare as JSON. Block boundaries are not preserved."
        ),
        "source_event_types": dict(Counter(event["type"] for event in original)),
        "converted_event_types": dict(Counter(event["type"] for event in rebuilt)),
        "native_metadata_not_carried": sorted(
            set(original[0]["payload"]) - set(rebuilt[0]["payload"])
        ),
        "source_reasoning_steps": sum(bool(step.reasoning_content) for step in trajectory.steps),
        "documented_losses": [
            "native world state, turn context, base instructions and runtime metadata",
            "message channels and step reasoning fields are not reconstructed",
            "token counters / ATIF metrics do not seed native session billing counters",
            "filesystem, processes and pending tools are not part of ATIF",
            "text block boundaries are flattened; comparison joins text blocks",
        ],
        "rtv_adjustments": {
            "cwd": "/workspace",
            "cli_version": CODEX_VERSION,
            "session_identity": "new UUID; source retained in lineage",
        },
    }


def export_atif(native: Path, output: Path, *, model: str) -> tuple[Path, dict[str, Any]]:
    """No model calls. Keep the raw ATIF and reconstructed transcript in ignored work/."""
    import shutil

    meta = read_session(native)
    for line in native.read_text().splitlines():
        event = json.loads(line)
        payload = event.get("payload", {})
        if event.get("type") == "response_item" and payload.get("type") == "message":
            if any(
                block.get("type") not in {"input_text", "output_text", "text"}
                for block in payload.get("content", [])
            ):
                raise ValueError("Non-text native messages are outside this ATIF experiment")
        if event.get("type") == "response_item" and payload.get("type") in {
            "function_call_output",
            "custom_tool_call_output",
        }:
            content = payload.get("output")
            if isinstance(content, list) and any(
                not isinstance(block, dict)
                or block.get("type") not in {"input_text", "output_text", "text"}
                for block in content
            ):
                raise ValueError("Non-text native tool results are outside this ATIF experiment")
    output.mkdir(parents=True, exist_ok=False)
    session_dir = output / "conversion-input/sessions/2000/01/01"
    session_dir.mkdir(parents=True)
    shutil.copy2(native, session_dir / native.name)
    agent = atif_codex_class()(
        logs_dir=output / "conversion-logs", model_name=model, version=CODEX_VERSION
    )
    trajectory = agent.convert_trajectory(output / "conversion-input")
    if trajectory is None:
        raise ValueError("Harbor could not export the native session to ATIF")
    # ATIF import is a new semantic branch. A matching UUID would not prove losslessness.
    trajectory.session_id = str(uuid4())
    trajectory.extra = {**(trajectory.extra or {}), "rtv_source_session": meta["id"]}
    # Harbor estimates API prices; they are not bills for ChatGPT subscription runs.
    for step in trajectory.steps:
        if step.metrics:
            step.metrics.cost_usd = None
    if trajectory.final_metrics:
        trajectory.final_metrics.total_cost_usd = None
    path = output / "trajectory.json"
    path.write_text(json.dumps(trajectory.to_json_dict(), indent=2) + "\n")
    validate_atif(path, model=model)
    name, rebuilt = agent.atif_to_native_trajectory(trajectory, trajectory.session_id)
    (output / name).write_text(rebuilt)
    audit = conversion_audit(native, rebuilt, trajectory)
    (output / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    return path, audit
