"""Context and transport boundaries without services or paid calls."""

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from rtv.mini_agent import MINI_MODEL, api_input, measured_usage
from rtv.runtime.handoff import plan, target_spec


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


@pytest.mark.parametrize("lost_reply", [False, True])
def test_real_mini_loop_with_mocked_transport_accounts_before_parse_and_never_retries(
    tmp_path, monkeypatch, lost_reply
):
    pytest.importorskip("psycopg")
    # Set before importing upstream, which otherwise reads its global dotenv file.
    monkeypatch.setenv("MSWEA_GLOBAL_CONFIG_DIR", str(tmp_path / "mini-config"))
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")
    pytest.importorskip("minisweagent")
    from rtv.mini_agent import execute_mini

    calls, settled, clients = [], [], []

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

        def settle_call(self, row, request, usage, *, cost_usd=None):
            settled.append((request, usage, cost_usd))

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
            if lost_reply:
                raise TimeoutError("lost response; must not retry")
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
                            {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}
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
    invocation = execute_mini(
        None,
        "Test submission",
        tmp_path,
        settings=None,
        row={"spec": {"model": MINI_MODEL}},
        operation="stage",
    )
    if lost_reply:
        with pytest.raises(RuntimeError, match="API outcome unknown"):
            asyncio.run(invocation)
        assert len(calls) == 1
        assert settled == [("request-1", None, None)]
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
