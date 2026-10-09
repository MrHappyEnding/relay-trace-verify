"""Context and transport boundaries without services or paid calls."""

import asyncio
import json
import runpy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from rtv.mini_agent import (
    MINI_MODEL,
    api_error_details,
    api_input,
    check_submission,
    measured_usage,
)
from rtv.runtime.handoff import plan, target_spec


@pytest.mark.parametrize("approved,prior", [(0, 0), (float("nan"), 0), (8, 7), (8, float("nan"))])
def test_smoke_budget_rejects_invalid_or_excess_carryover_before_credentials(approved, prior):
    pytest.importorskip("psycopg")
    script = runpy.run_path(str(Path(__file__).parents[1] / "scripts/m3_model_smoke.py"))
    args = SimpleNamespace(
        allow_model_calls=True,
        allow_api_calls=True,
        approved_budget_usd=approved,
        prior_reserved_usd=prior,
        api_call_limit=8,
    )
    # Deliberately no credential or output paths: refusal must precede their access.
    with pytest.raises(ValueError):
        script["main"](args)


@pytest.fixture
def source():
    return {
        "adapter": "codex",
        "model": "gpt-6-astra",
        "task_digest": "task",
        "image_id": "image",
        "ttl_seconds": 300,
        "delay_seconds": 0,
        "stages": 2,
        "max_recoveries": 0,
        "pause_after_checkpoint": True,
        "isolated_verifier": True,
    }


def test_paired_plan_freezes_task_environment_and_equal_budget(source):
    branches = plan(source, adapter="mini", portable=True, ttl=240)
    assert [b["label"] for b in branches] == [
        "A-continue",
        "B-workspace",
        "B-summary",
        "B-trajectory",
    ]
    assert [b["policy"] for b in branches] == ["native", "task-only", "summary", "portable"]
    assert all(b["spec"]["image_id"] == source["image_id"] for b in branches)
    assert all(b["spec"]["task_digest"] == source["task_digest"] for b in branches)
    assert all(b["spec"]["ttl_seconds"] == 240 for b in branches)
    assert not any(b["spec"]["pause_after_checkpoint"] for b in branches)
    assert branches[-1]["spec"]["model"] == MINI_MODEL
    assert source["pause_after_checkpoint"]  # source request is immutable


@pytest.mark.parametrize("adapter,model", [("mini", MINI_MODEL), ("codex", "another-model")])
def test_native_context_cannot_cross_agent_or_model(source, adapter, model):
    with pytest.raises(ValueError, match="same adapter and model"):
        target_spec(source, adapter, model, "native")


def test_cached_input_is_a_subset_and_missing_usage_stays_unknown():
    usage, cost = measured_usage(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "input_tokens_details": {"cached_tokens": 800},
            }
        }
    )
    assert usage["input_tokens"] == 1000
    assert cost == pytest.approx((200 * 0.75 + 800 * 0.075 + 100 * 4.5) / 1_000_000)
    assert measured_usage({}) == (None, None)
    assert measured_usage({"usage": {"input_tokens": 4, "output_tokens": -1}}) == (None, None)
    assert measured_usage(
        {
            "usage": {
                "input_tokens": 4,
                "output_tokens": 1,
                "input_tokens_details": {"cached_tokens": 5},
            }
        }
    ) == (None, None)


def test_response_projection_drops_local_metadata_without_dropping_tool_pair():
    messages = [
        {"role": "user", "content": "task", "extra": {"private": "local"}},
        {
            "object": "response",
            "output": [
                {"type": "function_call", "name": "bash", "call_id": "call-1", "arguments": "{}"}
            ],
            "usage": {"input_tokens": 5},
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "ok",
            "extra": {"raw_output": "ok"},
        },
    ]
    out = api_input(messages)
    assert [m.get("call_id") for m in out[1:]] == ["call-1", "call-1"]
    assert "extra" not in json.dumps(out)
    assert "usage" not in json.dumps(out)


def test_invalid_ttl_and_policy_rejected_without_docker(source):
    with pytest.raises(ValueError):
        plan(source, adapter="mini-fixture", ttl=0)
    with pytest.raises(ValueError):
        target_spec(source, "mini-fixture", None, "full-process-memory")


def test_api_projection_omits_null_fields_and_preserves_reasoning_phase_and_tool_pair():
    items = [
        {
            "type": "reasoning",
            "id": "r",
            "summary": [],
            "status": None,
            "encrypted_content": "opaque-reasoning",
        },
        {
            "type": "message",
            "id": "m",
            "role": "assistant",
            "status": "completed",
            "phase": "commentary",
            "content": [
                {"type": "output_text", "text": "working", "annotations": [], "logprobs": None}
            ],
        },
        {
            "type": "function_call",
            "id": "f",
            "name": "bash",
            "call_id": "call-1",
            "arguments": '{"command":"echo null"}',
            "namespace": None,
            "caller": None,
        },
    ]
    result = api_input(
        [
            {"object": "response", "output": items},
            {"type": "function_call_output", "call_id": "call-1", "output": "null"},
        ]
    )
    assert "status" not in result[0]
    assert result[0]["encrypted_content"] == "opaque-reasoning"
    assert result[1]["phase"] == "commentary"
    assert "logprobs" not in result[1]["content"][0]
    assert "namespace" not in result[2] and "caller" not in result[2]
    assert result[2]["arguments"] == items[2]["arguments"]
    assert result[2]["call_id"] == result[3]["call_id"]
    assert items[0]["status"] is None  # Never mutate the archived response.


@pytest.mark.parametrize("kind", ["reasoning", "function_call"])
def test_sdk_output_to_input_contract_rejects_original_nulls_and_accepts_projection(kind):
    pytest.importorskip("openai")
    from openai.types.responses.response_function_tool_call_param import (
        ResponseFunctionToolCallParam,
    )
    from openai.types.responses.response_reasoning_item_param import ResponseReasoningItemParam
    from pydantic import TypeAdapter, ValidationError

    if kind == "reasoning":
        item = {
            "type": kind,
            "id": "r",
            "summary": [],
            "status": None,
            "encrypted_content": "opaque-reasoning",
        }
        schema = ResponseReasoningItemParam
    else:
        item = {"type": kind, "name": "bash", "call_id": "c", "arguments": "{}", "namespace": None}
        schema = ResponseFunctionToolCallParam
    validator = TypeAdapter(schema)
    with pytest.raises(ValidationError):
        validator.validate_python(item)
    projected = api_input([{"object": "response", "output": [item]}])[0]
    validated = validator.validate_python(projected)
    assert json.loads(validator.dump_json(validated)) == projected


def test_api_error_diagnostics_exclude_arbitrary_provider_text():
    error = SimpleNamespace(
        status_code=429,
        code="insufficient_quota",
        type="insufficient_quota",
        message="private response contents",
        request_id="req_example-123",
        headers={"authorization": "secret"},
    )
    details = api_error_details(error)
    assert details["http_status"] == 429
    assert details["code"] == "insufficient_quota"
    assert details["request_id"] == "req_example-123"
    assert "private" not in json.dumps(details)
    assert "secret" not in json.dumps(details)
    error.code = error.type = error.request_id = "unexpected-sensitive-text"
    assert api_error_details(error) == {"exception": "SimpleNamespace", "http_status": 429}


@pytest.mark.parametrize("failure_kind", [None, "timeout", "quota"])
def test_real_mini_loop_with_mocked_transport_accounts_before_parse_and_never_retries(
    tmp_path, monkeypatch, failure_kind
):
    pytest.importorskip("psycopg")
    # Set before importing upstream, which otherwise reads its global dotenv file.
    monkeypatch.setenv("MSWEA_GLOBAL_CONFIG_DIR", str(tmp_path / "mini-config"))
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")
    pytest.importorskip("minisweagent")
    from rtv.mini_agent import execute_mini

    calls, settled, clients, failures = [], [], [], []

    class Store:
        def __init__(self, settings):
            pass

        @contextmanager
        def owned(self, row):
            yield None

        def begin_model_call(self, row, operation, n, reservation):
            assert n == len(calls) + 1
            assert len(settled) == len(calls)
            return f"request-{n}"

        def settle_call(self, row, request, usage, *, cost_usd=None, failure=None):
            settled.append((request, usage, cost_usd))
            if failure:
                failures.append(failure)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            assert kwargs["base_url"] == "https://api.openai.com/v1"
            self.responses = self
            self.closed = False
            clients.append(self)

        def create(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["max_output_tokens"] == 4096
            if failure_kind == "timeout":
                raise TimeoutError("lost response; must not retry")
            if failure_kind == "quota":
                import httpx
                from openai import RateLimitError

                raise RateLimitError(
                    "private-provider-text",
                    response=httpx.Response(
                        429, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
                    ),
                    body={"code": "insufficient_quota", "type": "insufficient_quota"},
                )
            # First response is billed but malformed; second submits successfully.
            data = {
                "object": "response",
                "output": []
                if len(calls) == 1
                else [
                    {
                        "type": "function_call",
                        "name": "bash",
                        "call_id": "submit",
                        "arguments": json.dumps(
                            {
                                "command": "cd /workspace && "
                                "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
                            }
                        ),
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 10},
            }
            return SimpleNamespace(model_dump=lambda **kw: data)

        def close(self):
            self.closed = True

    monkeypatch.setattr("rtv.runtime.store.Store", Store)
    monkeypatch.setattr("openai.OpenAI", Client)
    monkeypatch.setenv("RTV_ALLOW_API_CALLS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-never-sent")
    monkeypatch.delenv("RTV_API_KEY_FILE", raising=False)

    class Environment:
        async def exec(self, *, command, cwd, timeout_sec):
            assert command == "cd /workspace && echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            assert cwd == "/workspace"
            assert len(settled) == 2  # Bill the response before executing its tool.
            return SimpleNamespace(
                stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", stderr="", return_code=0
            )

    invocation = execute_mini(
        Environment(),
        "Test submission",
        tmp_path,
        settings=None,
        row={"spec": {"model": MINI_MODEL}},
        operation="stage",
    )
    if failure_kind:
        with pytest.raises(RuntimeError, match="API outcome unknown"):
            asyncio.run(invocation)
        assert len(calls) == 1
        assert settled == [("request-1", None, None)]
        if failure_kind == "quota":
            assert failures[0]["http_status"] == 429
            assert failures[0]["code"] == "insufficient_quota"
            assert "private-provider-text" not in (tmp_path / "mini-trajectory.json").read_text()
    else:
        result = asyncio.run(invocation)
        assert result.exit_status == "Submitted"
        assert len(calls) == len(settled) == 2
        saved = json.loads(result.rollout.read_text())
        assert saved["info"]["model_stats"]["instance_cost"] == pytest.approx(
            sum(observation[2] for observation in settled)
        )
        assert "test-only-never-sent" not in result.rollout.read_text()
    assert clients[0].closed


@pytest.mark.parametrize(
    "text,returncode,submission",
    [
        ("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 0, ""),
        ("\n  COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\npatch\n", 0, "patch\n"),
        ("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 1, None),
        ("log\nCOMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", 0, None),
        ("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT_suffix\n", 0, None),
        ("", 0, None),
    ],
)
def test_submission_matches_upstream_output_protocol(
    tmp_path, monkeypatch, text, returncode, submission
):
    monkeypatch.setenv("MSWEA_GLOBAL_CONFIG_DIR", str(tmp_path / "mini-config"))
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")
    pytest.importorskip("minisweagent")
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.exceptions import Submitted

    output = {"output": text, "returncode": returncode}
    upstream = LocalEnvironment()
    for check in (check_submission, upstream._check_finished):
        if submission is None:
            check(output)
        else:
            with pytest.raises(Submitted) as caught:
                check(output)
            assert caught.value.messages[0]["extra"]["submission"] == submission
