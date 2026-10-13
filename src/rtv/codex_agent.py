"""Small, fail-closed boundary around Harbor's optional Codex adapter.

No model SDK is imported until a caller explicitly starts a turn. Native state
is version-bound; semantic handoff deliberately does not import that state.
"""

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

CODEX_VERSION = "0.160.1"
DEFAULT_MODEL = "gpt-6-astra"
MODEL_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\Z")


def validate_auth(path: Path) -> Path:
    """Accept only a ChatGPT cache, never silently fall back to metered API auth."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("Cannot read a valid ChatGPT auth cache") from exc
    if not isinstance(data, dict) or data.get("OPENAI_API_KEY") or not data.get("tokens"):
        raise ValueError("This probe requires ChatGPT subscription auth without an API key")
    return path.resolve()


@dataclass(frozen=True)
class TurnResult:
    session_id: str
    rollout: Path
    usage: dict[str, int] | None
    session_usage: dict[str, int] | None = None
    prefix_boundary: dict[str, Any] | None = None


def session_usage(path: Path) -> dict[str, int] | None:
    """Codex 0.160.1 turn.completed reports session-cumulative counters on resume."""
    latest = None
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            payload = event.get("payload", {})
            if event.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") or {}
                if info.get("total_token_usage") is not None:
                    latest = info["total_token_usage"]
    return latest


def incremental_usage(
    current: dict[str, int], prior: dict[str, int] | None
) -> dict[str, int] | None:
    if prior is None or any(
        type(prior.get(key)) is not int or prior[key] > value or prior[key] < 0
        for key, value in current.items()
    ):
        # Missing/reset counters are unknown, never fabricated as zero cost.
        return None
    return {key: value - prior[key] for key, value in current.items()}


def read_session(path: Path, *, workspace: str = "/workspace") -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Native session must be a regular file")
    with path.open() as stream:
        first = json.loads(stream.readline())
    if first.get("type") != "session_meta":
        raise ValueError("Native session is missing session_meta")
    meta = first["payload"]
    UUID(meta["id"])
    if meta.get("cli_version") != CODEX_VERSION:
        raise ValueError("Native session CLI version does not match the pinned adapter")
    if workspace not in {"/workspace", "/testbed"} or meta.get("cwd") != workspace:
        raise ValueError("Native session has an incompatible workspace path")
    return meta


def read_turn(
    logs: Path, *, native: Path | None = None, workspace: str = "/workspace"
) -> TurnResult:
    """A zero pipeline exit alone is insufficient (the SDK pipes through tee)."""
    events = []
    for line in (logs / "codex.txt").read_text().splitlines():
        if line.lstrip().startswith("{"):
            events.append(json.loads(line))
    if any(event.get("type") in {"turn.failed", "error"} for event in events):
        raise ValueError("Codex reported a failed turn; see local agent logs")
    starts = [event for event in events if event.get("type") == "thread.started"]
    ends = [event for event in events if event.get("type") == "turn.completed"]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("Expected one completed Codex turn and one session identity")
    session_id = str(UUID(starts[0]["thread_id"]))
    paths = list((logs / "sessions").rglob("rollout-*.jsonl"))
    if len(paths) != 1:
        raise ValueError("Expected exactly one native session, refusing ambiguous resume")
    if read_session(paths[0], workspace=workspace)["id"] != session_id:
        raise ValueError("Event and native session identities disagree")
    if native is not None and read_session(native, workspace=workspace)["id"] != session_id:
        raise ValueError("Native recovery unexpectedly created a different session")
    usage = ends[0].get("usage", {})
    if not isinstance(usage, dict) or any(type(v) is not int or v < 0 for v in usage.values()):
        raise ValueError("Malformed turn usage")
    delta = usage if native is None else incremental_usage(usage, session_usage(native))
    if not usage:
        delta = None
    return TurnResult(session_id, paths[0], delta, usage)


def validate_bundle(bundle: Path, *, model: str, workspace: str = "/workspace") -> Path:
    """Call after the checkpoint store has verified every file's content hash."""
    metadata = json.loads((bundle / "session.json").read_text())
    if metadata.get("schema_version") != 1 or metadata.get("cli_version") != CODEX_VERSION:
        raise ValueError("Unsupported native checkpoint version")
    if metadata.get("model") != model:
        raise ValueError("Native recovery requires the original model in this probe")
    if metadata.get("workspace_scope", "/workspace") != workspace:
        raise ValueError("Native checkpoint workspace scope changed")
    files = [
        path
        for path in (bundle / "native").iterdir()
        if not (workspace == "/testbed" and path.name == ".DS_Store")
    ]
    if len(files) != 1:
        raise ValueError("Native checkpoint must contain exactly one rollout")
    meta = read_session(files[0], workspace=workspace)
    if meta["id"] != metadata.get("session_id"):
        raise ValueError("Checkpoint session identity mismatch")
    if not (bundle / "workspace").is_dir():
        raise ValueError("Native checkpoint has no workspace")
    return files[0]


class HarborCodexAdapter:
    """Asynchronous M0 adapter; reuses Harbor execution, auth and native import.

    This does not pretend to implement the synchronous fixture AgentAdapter.
    Cancellation must be followed by sandbox termination by the owning runner.
    """

    def __init__(self, *, model: str, auth_file: Path, workspace: str = "/workspace"):
        if not MODEL_NAME.fullmatch(model):
            raise ValueError("Model must be a plain model identifier")
        if any(os.environ.get(key) for key in ("OPENAI_BASE_URL", "OPENAI_API_BASE")):
            raise ValueError("Custom API endpoints are outside this subscription-only probe")
        self.model = model
        self.auth_file = validate_auth(auth_file)
        if workspace not in {"/workspace", "/testbed"}:
            raise ValueError("Unsupported Codex workspace")
        self.workspace = workspace

    async def execute(
        self,
        env: Any,
        instruction: str,
        *,
        native: Path | None = None,
        atif: Path | None = None,
        tool_prefix: dict | None = None,
    ) -> TurnResult:
        from harbor.agents.installed.codex import Codex
        from harbor.models.agent.context import AgentContext

        if native is not None and atif is not None:
            raise ValueError("Choose either native recovery or ATIF import")
        config = {"forced_login_method": "chatgpt", "features": {"multi_agent": False}}
        hook_options = {}
        if tool_prefix is not None:
            from rtv import codex_gate

            codex_gate.policy(tool_prefix)
            if native is not None or atif is not None or self.workspace != "/testbed":
                raise ValueError("Tool-prefix gate requires a fresh repository Codex turn")
            logs = env.trial_paths.agent_dir
            logs.mkdir(parents=True, exist_ok=True)
            receipt = logs / "tool-gate.json"
            with receipt.open("x") as stream:
                json.dump(codex_gate.initial(tool_prefix), stream)
            await env.upload_file(Path(codex_gate.__file__), codex_gate.REMOTE_SCRIPT)
            await env.upload_file(receipt, codex_gate.REMOTE_STATE)
            config["hooks"] = codex_gate.hook_config()
            # Only our vetted, uploaded hook in this disposable sandbox. Host trust is untouched.
            hook_options["dangerously_bypass_hook_trust"] = True
            instruction += codex_gate.prefix_instruction(tool_prefix)
        expected_atif_id = None
        if atif is not None:
            if self.workspace != "/workspace":
                raise ValueError("ATIF import is not verified for repository tasks")
            from rtv.codex_atif import atif_codex_class, validate_atif

            expected_atif_id = validate_atif(atif, model=self.model).session_id
            Codex = atif_codex_class()
        if native is not None:
            read_session(native, workspace=self.workspace)
        # No host config, skills, MCP connections or session directory is mounted.
        agent = Codex(
            logs_dir=env.trial_paths.agent_dir,
            model_name=self.model,
            version=CODEX_VERSION,
            extra_env={"CODEX_AUTH_JSON_PATH": str(self.auth_file)},
            config=config,
            reasoning_effort="low",
            web_search="disabled",
            load_trajectory=native or atif,
            **hook_options,
        )
        # Never let Harbor's generic installer silently replace this pinned image.
        version = await env.exec(command="codex --version", timeout_sec=15)
        if version.return_code or (version.stdout or "").strip() != f"codex-cli {CODEX_VERSION}":
            raise ValueError("Container has the wrong Codex CLI version")

        async def processes():
            observed = await env.exec(
                command="python " + codex_gate.REMOTE_SCRIPT + " --processes", timeout_sec=15
            )
            if observed.return_code:
                raise ValueError("Cannot verify tool-prefix process boundary")
            return json.loads(observed.stdout)

        before = await processes() if tool_prefix is not None else None
        action = agent.load if native is not None or atif is not None else agent.run
        await action(instruction, env, AgentContext())
        result = read_turn(env.trial_paths.agent_dir, native=native, workspace=self.workspace)
        if tool_prefix is not None:
            codex_gate.check_processes(before, await processes())
            await env.download_file(codex_gate.REMOTE_STATE, receipt)
            result = replace(result, prefix_boundary=codex_gate.audit(receipt, result, tool_prefix))
        if expected_atif_id is not None and result.session_id != expected_atif_id:
            raise ValueError("ATIF import did not select the prepared semantic session")
        return result
