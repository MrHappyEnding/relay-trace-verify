"""Gate state/failure contracts; these tests do not use a model or Docker."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rtv.codex_gate import audit, handle, initial, policy, transition

POLICY = {"version": "codex-tools-v1", "max_tools": 2}


def payload(event, cid="one", **changes):
    return dict(
        hook_event_name=event,
        session_id="session",
        turn_id="turn",
        tool_name="Bash",
        tool_use_id=cid,
        tool_input={"command": "fixture"},
        **changes,
    )


def receipt(tmp_path, tools=2):
    state = initial(POLICY)
    transition(state, payload("SessionStart"))
    items = []
    for n in range(tools):
        transition(state, payload("PreToolUse", str(n)))
        transition(state, payload("PostToolUse", str(n)))
        items.append(
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "turn_id": "turn",
                    "item": {"type": "CommandExecution", "id": str(n), "status": "completed"},
                },
            }
        )
    transition(state, payload("Stop"))
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(state))
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("\n".join(map(json.dumps, items)))
    return path, SimpleNamespace(session_id="session", rollout=rollout), state


def test_budget_denies_new_tools_but_allows_existing_completion():
    state = initial(POLICY)
    transition(state, payload("SessionStart"))
    for cid in ("one", "two"):
        assert transition(state, payload("PreToolUse", cid)) == {}
    denied = transition(state, payload("PreToolUse", "three"))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    # Completion order need not match admission order; a failed command still consumes a slot.
    for cid in ("two", "one"):
        assert (
            "additionalContext"
            in transition(state, payload("PostToolUse", cid))["hookSpecificOutput"]
        )
    transition(state, payload("Stop"))
    assert not state["errors"]


def test_concurrent_hook_processes_cannot_over_admit(tmp_path):
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(initial(POLICY)))
    handle(path, payload("SessionStart"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        outputs = list(pool.map(lambda n: handle(path, payload("PreToolUse", str(n))), range(16)))
    assert sum(not value for value in outputs) == 2
    assert sum(e.get("decision") == "allow" for e in json.loads(path.read_text())["events"]) == 2


@pytest.mark.parametrize(
    "tools,trigger", [(0, "natural_completion"), (1, "natural_completion"), (2, "tool_budget")]
)
def test_audit_retains_early_completion_without_selecting_on_reward(tmp_path, tools, trigger):
    path, turn, _ = receipt(tmp_path, tools)
    result = audit(path, turn, POLICY)
    assert result["trigger"] == trigger and result["completed_tools"] == tools


@pytest.mark.parametrize(
    "damage",
    [
        "missing-start",
        "missing-stop",
        "missing-post",
        "extra-tool",
        "pending",
        "session",
        "policy",
        "tool-input",
        "hook-error",
    ],
)
def test_incomplete_or_uncovered_execution_cannot_seal(tmp_path, damage):
    path, turn, state = receipt(tmp_path)
    if damage == "missing-start":
        state["events"].pop(0)
    elif damage == "missing-stop":
        state["events"].pop()
    elif damage == "missing-post":
        state["events"].pop(2)
    elif damage == "extra-tool":
        turn.rollout.write_text(turn.rollout.read_text().replace('"id": "0"', '"id": "unknown"'))
    elif damage == "pending":
        state["events"].insert(-1, {"event": "PreToolUse", "id": "3", "decision": "allow"})
    elif damage == "session":
        state["session_id"] = "another-session"
    elif damage == "policy":
        state["policy"]["max_tools"] = 3
    elif damage == "tool-input":
        state["events"][2]["input_sha256"] = "changed"
    else:
        state["errors"].append("invalid_hook_event")
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        audit(path, turn, POLICY)


def test_invalid_hook_leaves_durable_error_receipt(tmp_path):
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(initial(POLICY)))
    with pytest.raises(ValueError):
        handle(path, payload("PostToolUse"))
    assert json.loads(path.read_text())["errors"] == ["invalid_hook_event"]


def test_stop_with_active_tool_is_not_a_safe_checkpoint():
    state = initial(POLICY)
    transition(state, payload("SessionStart"))
    transition(state, payload("PreToolUse"))
    transition(state, payload("Stop"))
    assert state["errors"] == ["unfinished_tools_at_stop"]


def test_detached_process_or_reused_pid_prevents_sealing():
    from rtv.codex_gate import check_processes

    check_processes(["20:100"], [])
    with pytest.raises(ValueError, match="running processes"):
        check_processes(["20:100"], ["20:101"])
    with pytest.raises(ValueError, match="running processes"):
        check_processes([], ["21:100"])


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "6"])
def test_policy_cannot_silently_change_units(limit):
    with pytest.raises(ValueError):
        policy({**POLICY, "max_tools": limit})
