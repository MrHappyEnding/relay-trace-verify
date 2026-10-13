"""Cooperative tool-budget yield for pinned Codex; also runs as a standalone hook.

This is an experiment boundary, not a sandbox against a malicious agent. A hook
failure must invalidate checkpoint eligibility even if Codex continues execution.
"""

import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path

VERSION = "codex-tools-v1"
RUNTIME_NOTICE_VERSION = "codex-tools-v2"
REMOTE_SCRIPT = "/tmp/rtv-codex-gate.py"
REMOTE_STATE = "/logs/agent/tool-gate.json"
NOTICE = (
    "RTV tool budget reached. Do not start more tools. Wait for already-started tools "
    "to finish, then end this turn with a concise handoff: changes, public tests, and "
    "remaining work. Do not claim the task is solved without evidence."
)


def policy(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "max_tools"}
        or value["version"] not in (VERSION, RUNTIME_NOTICE_VERSION)
        or type(value["max_tools"]) is not int
        or not 1 <= value["max_tools"] <= 100
    ):
        raise ValueError("Invalid Codex tool-prefix policy")
    return dict(value)


def prefix_instruction(value):
    value = policy(value)
    restrictions = (
        "Use only shell and apply_patch tools (write_stdin may finish "
        "an existing shell). Do not background/detach commands or change RTV hooks. "
    )
    ending = (
        "finish this turn with changes, public test results and remaining work. "
        "You may finish earlier; do not introduce a defect."
    )
    if value["version"] == VERSION:
        return (
            f"\nRTV prefix allowance: at most {value['max_tools']} Bash/apply_patch "
            "tool admissions. " + restrictions + "When the allowance is reached, " + ending
        )
    # Keep the predeclared quota in the receipt, but do not tell the model its
    # numeric value up front. The same PostToolUse notice defines the boundary.
    return (
        "\nRTV may request a handoff during execution. "
        + restrictions
        + "When a tool hook requests handoff, "
        + ending
    )


def initial(value):
    return {"schema_version": 1, "policy": policy(value), "events": [], "errors": []}


def hook_config(script=REMOTE_SCRIPT, state=REMOTE_STATE):
    import shlex

    command = "python " + shlex.quote(script) + " " + shlex.quote(state)
    return {
        name: [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]
        for name in ("SessionStart", "PreToolUse", "PostToolUse", "Stop")
    }


def process_snapshot():
    """Linux PID/start-time identities, excluding this observer and its ancestors."""
    processes = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            processes[int(entry.name)] = (fields[0], int(fields[1]), fields[19])
        except FileNotFoundError:
            continue
    ancestors, pid = set(), os.getpid()
    while pid in processes and pid not in ancestors:
        ancestors.add(pid)
        pid = processes[pid][1]
    return sorted(
        f"{pid}:{value[2]}"
        for pid, value in processes.items()
        if pid not in ancestors and value[0] != "Z"
    )


def check_processes(before, after):
    if set(after) - set(before):
        raise ValueError("Agent left running processes; refusing a tool-prefix checkpoint")


def transition(state, payload):
    """Called under flock. Receipts contain hashes/IDs, never commands or outputs."""
    limit = policy(state["policy"])["max_tools"]
    event = payload["hook_event_name"]
    sid = payload["session_id"]
    if not sid or (state.get("session_id") and state["session_id"] != sid):
        raise ValueError("Hook session identity changed")
    state["session_id"] = sid
    events = state["events"]
    admitted = {e["id"] for e in events if e.get("decision") == "allow"}
    completed = {e["id"] for e in events if e["event"] == "PostToolUse"}
    receipt = {"event": event}
    output = {}
    if event in {"PreToolUse", "PostToolUse"}:
        tid = payload["turn_id"]
        if not tid or (state.get("turn_id") and state["turn_id"] != tid):
            raise ValueError("Hook turn identity changed")
        state["turn_id"] = tid
        cid, name = payload["tool_use_id"], payload["tool_name"]
        if not cid:
            raise ValueError("Missing tool identity")
        receipt.update(id=cid, tool=name)
        receipt["input_sha256"] = hashlib.sha256(
            json.dumps(payload["tool_input"], sort_keys=True).encode()
        ).hexdigest()
        if event == "PreToolUse":
            if any(e.get("id") == cid for e in events):
                raise ValueError("Duplicate tool admission")
            if name not in {"Bash", "apply_patch"}:
                state["errors"].append("unsupported_tool")
            deny = bool(state["errors"]) or len(admitted) >= limit
            receipt["decision"] = "deny" if deny else "allow"
            if deny:
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "permissionDecision": "deny",
                        "permissionDecisionReason": NOTICE,
                    }
                }
        else:
            before = next((e for e in events if e.get("id") == cid), None)
            if (
                cid not in admitted
                or cid in completed
                or before["tool"] != name
                or before["input_sha256"] != receipt["input_sha256"]
            ):
                raise ValueError("Tool completion has no matching admission")
            if len(admitted) >= limit:
                output = {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": NOTICE,
                    }
                }
    elif event == "SessionStart":
        if events:
            raise ValueError("Unexpected repeated SessionStart")
    elif event == "Stop":
        if admitted != completed:
            state["errors"].append("unfinished_tools_at_stop")
    else:
        raise ValueError("Unsupported hook event")
    events.append(receipt)
    return output


def handle(path, payload):
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text())
        try:
            result = transition(state, payload)
        except (KeyError, ValueError, TypeError):
            state["errors"].append("invalid_hook_event")
            result = None
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if result is None:
            raise ValueError("Invalid hook event; prefix is not checkpointable")
        return result


def audit(path, turn, expected):
    """Require a completed turn, hook coverage and no outstanding admitted tools."""
    raw = path.read_bytes()
    state = json.loads(raw)
    events = state["events"]
    if (
        state.get("schema_version") != 1
        or state.get("policy") != policy(expected)
        or state.get("session_id") != turn.session_id
        or state.get("errors")
        or not events
        or events[0]["event"] != "SessionStart"
        or events[-1]["event"] != "Stop"
        or sum(e["event"] == "SessionStart" for e in events) != 1
        or sum(e["event"] == "Stop" for e in events) != 1
    ):
        raise ValueError("Incomplete or invalid tool-gate receipts")
    admitted, completed, denied = {}, set(), set()
    for event in events:
        cid = event.get("id")
        if event["event"] == "PreToolUse":
            if cid in admitted or cid in denied:
                raise ValueError("Duplicate tool receipt")
            if event["decision"] == "allow":
                if len(admitted) >= expected["max_tools"]:
                    raise ValueError("Tool budget exceeded")
                admitted[cid] = event
            elif event["decision"] == "deny":
                if len(admitted) != expected["max_tools"]:
                    raise ValueError("Premature tool denial")
                denied.add(cid)
            else:
                raise ValueError("Unknown admission decision")
        elif event["event"] == "PostToolUse":
            if (
                cid not in admitted
                or cid in completed
                or any(event[k] != admitted[cid][k] for k in ("tool", "input_sha256"))
            ):
                raise ValueError("Unmatched tool completion")
            completed.add(cid)
    if set(admitted) != completed:
        raise ValueError("Outstanding tool execution")
    executed = set()
    for line in turn.rollout.read_text().splitlines():
        record = json.loads(line)
        payload = record.get("payload", {})
        if record.get("type") != "event_msg" or payload.get("type") != "item_completed":
            continue
        if payload.get("turn_id") != state.get("turn_id") and admitted:
            raise ValueError("Prefix rollout contains another turn")
        item = payload.get("item", {})
        if item.get("type") in {"CommandExecution", "FileChange"}:
            cid = item["id"]
            if (
                cid not in completed
                or cid in executed
                or item.get("status") not in {"completed", "failed"}
            ):
                raise ValueError("Uncovered or unfinished native tool execution")
            executed.add(cid)
        elif item.get("type") not in {"UserMessage", "AgentMessage", "Reasoning"}:
            raise ValueError("Unsupported native item in bounded prefix")
    if executed != completed:
        raise ValueError("Native rollout does not account for completed tools")
    return {
        "policy": policy(expected),
        "admitted_tools": len(admitted),
        "completed_tools": len(completed),
        "denied_tools": len(denied),
        "trigger": "tool_budget"
        if len(admitted) == expected["max_tools"]
        else "natural_completion",
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "boundary": "cooperative completed turn; sandbox quiescence still required",
    }


if __name__ == "__main__":
    try:
        print(
            json.dumps(
                process_snapshot()
                if sys.argv[1] == "--processes"
                else handle(Path(sys.argv[1]), json.load(sys.stdin))
            )
        )
    except Exception:
        # Codex may fail open on other hook failures; host-side audit then refuses sealing.
        print("RTV tool gate failed; stop this turn without further tools.", file=sys.stderr)
        sys.exit(2)
