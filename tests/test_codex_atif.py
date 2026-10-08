import json
import sys
from uuid import uuid4

import pytest

from rtv.codex_agent import CODEX_VERSION, read_session
from rtv.codex_atif import export_atif, validate_atif


@pytest.fixture
def native(tmp_path):
    if sys.version_info < (3, 12):
        pytest.skip("Optional Harbor requires Python 3.12+")
    pytest.importorskip("harbor.models.trajectories")
    identity = str(uuid4())
    events = [
        {
            "type": "session_meta",
            "payload": {
                "id": identity,
                "cwd": "/workspace",
                "cli_version": CODEX_VERSION,
                "base_instructions": "fixture policy",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Remember fixture-marker."}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "call1",
                "input": "echo fixture",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call1",
                "output": [
                    {"type": "input_text", "text": "fixture"},
                    {"type": "input_text", "text": "done"},
                ],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Completed fixture."}],
            },
        },
    ]
    path = tmp_path / f"rollout-2026-10-08T00-00-00-{identity}.jsonl"
    path.write_text(
        "\n".join(json.dumps({"timestamp": "2026-10-08T00:00:00Z", **e}) for e in events) + "\n"
    )
    return path


def test_atif_retains_supported_content_but_changes_native_runtime_metadata(native, tmp_path):
    atif, audit = export_atif(native, tmp_path / "export", model="fixture-model")
    parsed = validate_atif(atif, model="fixture-model")
    assert parsed.session_id != read_session(native)["id"]
    assert parsed.extra["rtv_source_session"] == read_session(native)["id"]
    rebuilt = next(atif.parent.glob("rollout-*.jsonl"))
    assert read_session(rebuilt)["cwd"] == "/workspace"
    assert all(v["lost_or_changed"] == 0 for v in audit["content"].values())
    assert audit["content"]["results"]["retained_normalized"] == 1
    assert "base_instructions" in audit["native_metadata_not_carried"]
    assert "token_count" not in rebuilt.read_text()
    assert all(s.metrics is None or s.metrics.cost_usd is None for s in parsed.steps)


def test_atif_rejects_unverified_model_and_missing_tool_results(native, tmp_path):
    atif, _ = export_atif(native, tmp_path / "export", model="fixture-model")
    with pytest.raises(ValueError, match="same-model"):
        validate_atif(atif, model="another-model")
    data = json.loads(atif.read_text())
    for step in data["steps"]:
        if step.get("tool_calls"):
            step.pop("observation", None)
    atif.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="matching result"):
        validate_atif(atif, model="fixture-model")


def test_non_text_native_content_is_rejected_before_conversion(native, tmp_path):
    native.write_text(
        native.read_text()
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_image"}],
                },
            }
        )
        + "\n"
    )
    with pytest.raises(ValueError, match="Non-text"):
        export_atif(native, tmp_path / "export", model="fixture-model")
    assert not (tmp_path / "export").exists()
