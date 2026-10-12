"""mini-swe-agent 2.4.6 loop, Harbor-only tools, and a bounded host-side API transport."""

import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

MINI_VERSION = "2.4.6"
MINI_MODEL = "gpt-5.4-mini-2026-03-17"
REQUEST_BYTES = 40000
MAX_OUTPUT = 4096
RESERVATION_USD = 0.062


def api_error_details(exc):
    """Keep diagnostic enums, never raw response messages, headers or credentials."""
    details = {"exception": type(exc).__name__}
    status = getattr(exc, "status_code", None)
    if type(status) is int and 100 <= status <= 599:
        details["http_status"] = status
    allowed = {
        "insufficient_quota",
        "credit_balance_exhausted",
        "rate_limit_exceeded",
        "slow_down",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "invalid_api_key",
        "model_not_found",
        "context_length_exceeded",
        "server_is_overloaded",
        "rate_limit_error",
        "invalid_request_error",
        "server_error",
        "service_unavailable_error",
    }
    for name in ("code", "type"):
        value = getattr(exc, name, None)
        if isinstance(value, str) and value in allowed:
            details[name] = value
    param = getattr(exc, "param", None)
    if isinstance(param, str) and re.fullmatch(
        r"(?:input|model|tools|max_output_tokens|reasoning|include|store)[A-Za-z0-9_.\[\]]{0,120}",
        param,
    ):
        details["param"] = param
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"req_[A-Za-z0-9_-]{1,120}", request_id):
        details["request_id"] = request_id
    return details


def measured_usage(data):
    u = data.get("usage")
    if not isinstance(u, dict) or any(
        type(u.get(k)) is not int or u[k] < 0 for k in ("input_tokens", "output_tokens")
    ):
        return None, None
    details = u.get("input_tokens_details") or {}
    if not isinstance(details, dict):
        return None, None
    cached = details.get("cached_tokens", 0)
    if type(cached) is not int or not 0 <= cached <= u["input_tokens"]:
        return None, None
    usage = {
        "input_tokens": u["input_tokens"],
        "output_tokens": u["output_tokens"],
        "cached_input_tokens": cached,
    }
    cost = (
        (usage["input_tokens"] - cached) * 0.75 + cached * 0.075 + usage["output_tokens"] * 4.5
    ) / 1_000_000
    return usage, cost


def api_input(messages):
    def clean(value):
        # SDK output models materialize absent optional fields as None. The input
        # schema accepts omission but rejects null for e.g. reasoning.status and
        # function_call.namespace. Preserve non-null phase and opaque reasoning.
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k != "extra" and v is not None}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    result = []
    for message in messages:
        items = message.get("output", []) if message.get("object") == "response" else [message]
        result.extend(clean(item) for item in items)
    return result


def check_submission(output):
    """Adapt mini-swe-agent 2.4.6 LocalEnvironment._check_finished to Harbor output."""
    from minisweagent.exceptions import Submitted

    # Match upstream's output protocol, not the spelling of the shell command.
    # https://github.com/SWE-agent/mini-swe-agent/blob/v2.4.6/src/minisweagent/environments/local.py
    lines = output.get("output", "").lstrip().splitlines(keepends=True)
    if (
        lines
        and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        and output["returncode"] == 0
    ):
        submission = "".join(lines[1:])
        raise Submitted(
            {
                "role": "exit",
                "content": submission,
                "extra": {"exit_status": "Submitted", "submission": submission},
            }
        )


@dataclass
class MiniResult:
    rollout: Path
    exit_status: str
    api_calls: int
    synthetic: bool


async def execute_mini(env, instruction, output, *, settings, row, operation, scripted=False):
    # Upstream loads a global dotenv file on import. Give this invocation an empty,
    # controlled directory rather than reading any user's mini-swe configuration.
    os.environ["MSWEA_GLOBAL_CONFIG_DIR"] = str(output / "mini-config")
    os.environ["MSWEA_SILENT_STARTUP"] = "1"
    import minisweagent
    import yaml
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.exceptions import FormatError, LimitsExceeded
    from minisweagent.models.utils.actions_toolcall_response import (
        BASH_TOOL_RESPONSE_API,
        format_toolcall_observation_messages,
        parse_toolcall_actions_response,
    )

    from rtv.harbor_probe import FINAL_SOURCE
    from rtv.runtime.store import Store
    from rtv.runtime.tasks import contract, scripted_commands, workspace

    if minisweagent.__version__ != MINI_VERSION:
        raise ValueError("mini-swe-agent version mismatch")
    templates = yaml.safe_load((minisweagent.package_dir / "config/mini.yaml").read_text())
    store = Store(settings)
    scope = workspace(row["spec"])
    repository = contract(row["spec"])
    steps = row["spec"].get("api_call_limit", 8) if repository else 8
    if type(steps) is not int or not 1 <= steps <= 16:
        raise ValueError("Mini request limit must be 1–16")
    loop = asyncio.get_running_loop()
    stop = threading.Event()
    client = None
    if not scripted:
        if os.environ.get("RTV_ALLOW_API_CALLS") != "1" or row["spec"]["model"] != MINI_MODEL:
            raise ValueError("Metered mini requires worker opt-in and the pinned model")
        from openai import OpenAI

        keyfile = os.environ.get("RTV_API_KEY_FILE")
        key = Path(keyfile).read_text().strip() if keyfile else os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("API credential missing; no request sent")
        client = OpenAI(
            api_key=key, base_url="https://api.openai.com/v1", max_retries=0, timeout=45
        )

    def active():
        if stop.is_set():
            raise RuntimeError("Mini execution cancelled")
        with store.owned(row):
            pass

    class Model:
        config = SimpleNamespace(model_name=row["spec"]["model"] or "scripted-provider")
        n = 0

        def query(self, messages, **kwargs):
            active()
            if scripted:
                self.n += 1
                import shlex

                commands = [
                    "cat positive_sum.py",
                    "python -c "
                    + shlex.quote(
                        "from pathlib import Path; Path('positive_sum.py').write_text("
                        + repr(FINAL_SOURCE)
                        + ")"
                    ),
                    "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                ]
                if repository:
                    commands = [
                        *scripted_commands(row["spec"]),
                        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                    ]
                command = commands[min(self.n - 1, len(commands) - 1)]
                data = {
                    "object": "response",
                    "id": f"scripted-{self.n}",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "bash",
                            "call_id": f"call-{self.n}",
                            "arguments": json.dumps({"command": command}),
                        }
                    ],
                }
                cost = 0
            else:
                inputs = api_input(messages)
                if (
                    len(
                        json.dumps(
                            {"input": inputs, "tools": [BASH_TOOL_RESPONSE_API]}, ensure_ascii=False
                        ).encode()
                    )
                    > REQUEST_BYTES
                ):
                    # A local limit is a completed agent stop, not a transport
                    # failure. Preserve the patch/trajectory for isolated grading,
                    # without truncating history, reserving funds or making a call.
                    raise LimitsExceeded(
                        {
                            "role": "exit",
                            "content": "API input exceeds the bounded 40 KB request envelope",
                            "extra": {"exit_status": "InputLimitExceeded", "submission": ""},
                        }
                    )
                self.n += 1
                request = store.begin_model_call(row, operation, self.n, RESERVATION_USD)
                try:
                    response = client.responses.create(
                        model=MINI_MODEL,
                        input=inputs,
                        tools=[BASH_TOOL_RESPONSE_API],
                        parallel_tool_calls=False,
                        max_output_tokens=MAX_OUTPUT,
                        reasoning={"effort": "low"},
                        store=False,
                        include=["reasoning.encrypted_content"],
                    )
                except Exception as exc:
                    failure = api_error_details(exc)
                    store.settle_call(row, request, None, failure=failure)
                    raise RuntimeError("API outcome unknown: " + json.dumps(failure)) from None
                data = response.model_dump(mode="json")
                usage, cost = measured_usage(data)
                store.settle_call(row, request, usage, cost_usd=cost)
                if usage is None or cost > RESERVATION_USD:
                    raise RuntimeError("Uncertain or unexpectedly large API usage; stopping")
            # Settlement precedes parsing and subsequent actions: parse failure is billed.
            try:
                actions = parse_toolcall_actions_response(
                    data["output"],
                    format_error_template=templates["model"]["format_error_template"],
                )
            except FormatError as exc:
                exc.messages[0].setdefault("extra", {})["cost"] = cost or 0
                raise
            data["extra"] = {"actions": actions, "cost": cost or 0}
            return data

        def format_message(self, **kwargs):
            return kwargs

        def format_observation_messages(self, message, outputs, template_vars=None):
            return format_toolcall_observation_messages(
                actions=message["extra"]["actions"],
                outputs=outputs,
                observation_template=templates["model"]["observation_template"],
                template_vars=template_vars,
            )

        def get_template_vars(self, **kwargs):
            return {"model_name": self.config.model_name, **kwargs}

        def serialize(self):
            return {
                "info": {
                    "config": {
                        "model": {
                            "model_name": self.config.model_name,
                            "transport": "rtv-responses",
                            "synthetic": scripted,
                        }
                    }
                }
            }

    class Environment:
        config = SimpleNamespace(cwd=scope)

        def execute(self, action, cwd=""):
            active()
            command = action.get("command")
            if not isinstance(command, str):
                raise ValueError("Expected bash command text")

            async def invoke():
                return await env.exec(command=command, cwd=scope, timeout_sec=30)

            reply = asyncio.run_coroutine_threadsafe(invoke(), loop).result(timeout=40)
            observation = {
                "output": ((reply.stdout or "") + (reply.stderr or ""))[:20000],
                "returncode": reply.return_code,
                "exception_info": "",
            }
            check_submission(observation)
            return observation

        def get_template_vars(self, **kwargs):
            return {
                "system": "Linux",
                "release": "container",
                "version": "pinned image",
                "machine": "container",
                "cwd": scope,
                **kwargs,
            }

        def serialize(self):
            return {
                "info": {"config": {"environment": {"type": "HarborDockerBridge", "cwd": scope}}}
            }

    config = {k: v for k, v in templates["agent"].items() if k != "mode"}
    config.update(
        step_limit=steps,
        cost_limit=0,
        wall_time_limit_seconds=row["spec"].get("agent_seconds", 240),
        output_path=output / "mini-trajectory.json",
    )
    model = Model()
    agent = DefaultAgent(model, Environment(), **config)

    def run():
        try:
            return agent.run(instruction)
        finally:
            if client:
                client.close()

    job = asyncio.create_task(asyncio.to_thread(run))
    try:
        result = await asyncio.shield(job)
        return MiniResult(output / "mini-trajectory.json", result["exit_status"], model.n, scripted)
    finally:
        stop.set()
        # A running HTTP request is not replayed. The thread can settle its own
        # late observation, but active() prevents further requests or sandbox tools.
