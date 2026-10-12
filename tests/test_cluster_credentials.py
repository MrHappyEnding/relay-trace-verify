"""Credential admission/negative tests; no real secrets or provider requests."""

import base64
import json
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("psycopg")

from test_benchmark_execution import MockStore, pair
from test_cluster import configuration

from rtv.benchmark.execution import collect, prepare_pair, validate_run
from rtv.cluster import credentials
from rtv.cluster.kubernetes import job_manifest
from rtv.codex_agent import DEFAULT_MODEL
from rtv.mini_agent import MINI_MODEL
from rtv.runtime.config import Settings
from rtv.runtime.store import spec_hash


def real_pair(tmp_path):
    protocol, oracle = pair(tmp_path)
    source = {k: v for k, v in oracle["source"]["spec"].items() if not k.startswith("scripted_")}
    source.update(adapter="codex", model=DEFAULT_MODEL, isolated_verifier=True)
    return protocol, prepare_pair(protocol, source, tmp_path / "run", mode="real-model")


def admission(tmp_path):
    protocol, run = real_pair(tmp_path)
    refs = {a: {"name": f"rtv-model-test-{a}", "uid": f"uid-{a}"} for a in ("codex", "mini")}
    access = credentials.authorize(run, 2, refs)
    config = replace(
        configuration(tmp_path),
        max_jobs=1,
        cpu_millis=2000,
        memory_mb=5120,
        engine_disk_gib=16,
        repository_contract_sha256=run["source"]["spec"]["task_contract"]["contract_sha256"],
        model_access=access,
    )
    settings = Settings(tmp_path / "run", "", "", schema="rtv4")
    return protocol, run, config, settings


def row(entry):
    return {
        **entry,
        "spec_hash": spec_hash(entry["spec"]),
        "epoch": 1,
        "execution_id": "execution",
        "deadline": datetime.now(UTC) + timedelta(seconds=1800),
    }


def test_real_plan_pins_models_and_budget_without_controls(tmp_path):
    protocol, run = real_pair(tmp_path)
    validate_run(protocol, run)
    specs = [r["spec"] for r in run["entries"]]
    assert sum(s["api_budget_usd"] for s in specs) == 1.984
    assert all(s["model"] == MINI_MODEL for s in specs if s["adapter"] == "mini")
    assert not any("scripted_" in json.dumps(s) for s in specs)
    bad = deepcopy(run)
    bad["source"]["spec"]["scripted_source"] = ["oracle"]
    with pytest.raises(ValueError, match="no controls"):
        validate_run(protocol, bad)


def test_larger_envelope_is_frozen_reserved_and_bound_to_admission(tmp_path):
    protocol, historical = real_pair(tmp_path)
    source = {**historical["source"]["spec"], "api_envelope": "mini-160kb-v1"}
    run = prepare_pair(protocol, source, tmp_path / "large", mode="real-model")
    validate_run(protocol, historical)  # The omitted policy still means 40 KB / $0.062.
    validate_run(protocol, run)
    assert sum(e["spec"]["api_budget_usd"] for e in run["entries"]) == 5.12
    refs = {a: {"name": f"rtv-model-test-{a}", "uid": f"uid-{a}"} for a in ("codex", "mini")}
    with pytest.raises(ValueError, match="exceed approval"):
        credentials.authorize(run, 2, refs)
    access = credentials.authorize(run, 5.12, refs)
    entry = next(e for e in run["entries"] if e["spec"]["adapter"] == "mini")
    assert (
        credentials.admitted_reference({"model_access": access}, run["root"], row(entry))
        == refs["mini"]
    )
    # Lowering the grant's reservation policy cannot authorize a larger spec,
    # even when the saved spec hash and run identities are left intact.
    access["grants"][entry["trial_id"]]["api_envelope"] = "mini-40kb-v1"
    with pytest.raises(ValueError, match="outside the approved"):
        credentials.admitted_reference({"model_access": access}, run["root"], row(entry))
    bad = deepcopy(run)
    bad["entries"][0]["spec"]["api_envelope"] = "mini-40kb-v1"
    with pytest.raises(ValueError, match="assignment changed"):
        validate_run(protocol, bad)
    source["api_envelope"] = "unbounded"
    with pytest.raises(ValueError, match="Unknown immutable"):
        prepare_pair(protocol, source, tmp_path / "bad", mode="real-model")


@pytest.mark.parametrize("damage", ["budget", "trial", "epoch", "spec", "root", "adapter"])
def test_admission_fails_closed(tmp_path, damage):
    _, run, config, settings = admission(tmp_path)
    current = row(run["source"])
    pool = config.frozen()
    root = settings.root
    if damage == "budget":
        pool["model_access"]["approved_budget_usd"] = 1
    elif damage == "trial":
        current["trial_id"] = "unapproved"
    elif damage == "epoch":
        current["epoch"] = 2
    elif damage == "spec":
        current["spec"] = {**current["spec"], "agent_seconds": 600}
    elif damage == "adapter":
        current["spec"] = {**current["spec"], "adapter": "mini", "model": MINI_MODEL}
    else:
        root = tmp_path / "another"
    with pytest.raises(ValueError):
        credentials.admitted_reference(pool, root, current)


def test_per_adapter_secret_projection_and_no_auth_values_in_manifest(tmp_path):
    _, run, config, settings = admission(tmp_path)
    for entry in [run["source"], run["entries"][0]]:
        current = row(entry)
        pod = job_manifest(settings, config, current, {"job_name": "job"})["spec"]["template"][
            "spec"
        ]
        volumes = [v["secret"] for v in pod["volumes"] if "secret" in v]
        assert volumes == [
            {
                "secretName": "rtv-model-test-" + entry["spec"]["adapter"],
                "defaultMode": 0o400,
                "items": [{"key": "credential", "path": "credential"}],
            }
        ]
        assert not any(e["name"] in credentials.ENV_KEYS for e in pod["containers"][0]["env"])
        assert not pod["automountServiceAccountToken"]
    fixture = row(run["source"])
    fixture["spec"] = {"adapter": "fixture"}
    manifest = job_manifest(settings, config, fixture, {"job_name": "job"})
    assert all("secret" not in v for v in manifest["spec"]["template"]["spec"]["volumes"])


def test_environment_removes_inherited_auth_and_only_enables_target(tmp_path, monkeypatch):
    _, run, config, settings = admission(tmp_path)
    for key in credentials.ENV_KEYS:
        monkeypatch.setenv(key, "sentinel-inherited")
    mounted = tmp_path / "projected"
    mounted.mkdir()
    keyfile = mounted / "credential"
    keyfile.write_text("sentinel-projected-api-key")
    keyfile.chmod(0o400)
    monkeypatch.setattr(credentials, "MOUNT", str(mounted))
    mini = next(e for e in run["entries"] if e["spec"]["adapter"] == "mini")
    env = credentials.supervisor_environment(config.frozen(), settings.root, row(mini))
    assert env["RTV_ALLOW_API_CALLS"] == "1" and env["RTV_API_KEY_FILE"] == str(keyfile)
    assert "RTV_ALLOW_MODEL_CALLS" not in env and "OPENAI_API_KEY" not in env
    assert "sentinel" not in json.dumps(env)
    keyfile.chmod(0o644)
    with pytest.raises(ValueError, match="unsafe projected"):
        credentials.supervisor_environment(config.frozen(), settings.root, row(mini))
    fixture = row(run["source"])
    fixture["spec"] = {"adapter": "fixture"}
    env = credentials.supervisor_environment(config.frozen(), settings.root, fixture)
    assert not credentials.ENV_KEYS.intersection(env)


def test_secret_identity_cleanup_and_error_redaction(tmp_path):
    _, run, config, _ = admission(tmp_path)
    backend = SimpleNamespace(config=config, core=Mock())
    secret = {
        "metadata": {"uid": "uid-mini", "labels": {credentials.LABEL: run["run_sha256"][:63]}},
        "immutable": True,
        "data": {"credential": "not-logged"},
    }
    backend.core.read_namespaced_secret.return_value = SimpleNamespace(data=json.dumps(secret))
    ref = config.model_access["secrets"]["mini"]
    credentials.remove_secret(backend, config.model_access, ref)
    assert backend.core.delete_namespaced_secret.call_args.kwargs["body"] == {
        "preconditions": {"uid": "uid-mini"}
    }
    with pytest.raises(ValueError, match="identity"):
        credentials.remove_secret(backend, config.model_access, {**ref, "uid": "replacement"})
    backend.core.delete_namespaced_secret.assert_called_once()
    private = tmp_path.parent / (tmp_path.name + "-key")
    private.write_text("sentinel-do-not-log")
    private.chmod(0o600)
    backend.core.create_namespaced_secret.side_effect = RuntimeError("sentinel-do-not-log")
    try:
        with pytest.raises(RuntimeError) as caught:
            credentials.install_secret(backend, run["run_sha256"], "mini", private)
        assert "sentinel" not in str(caught.value) and caught.value.__suppress_context__
    finally:
        private.unlink()


def test_real_collection_rejects_controls_and_keeps_unknown(tmp_path):
    protocol, run = real_pair(tmp_path)
    store = MockStore(run)
    for expected in [run["source"], *run["entries"]]:
        ledger = store.ledgers[expected["trial_id"]]
        ledger["operations"][0]["kind"] = (
            "codex" if expected["spec"]["adapter"] == "codex" else "mini-api"
        )
    report = collect(protocol, run, store, tmp_path / "good")
    assert report["mode"] == "real-model" and report["missing"] == 12
    store.ledgers[run["entries"][0]["trial_id"]]["operations"][0]["kind"] = "fixture"
    with pytest.raises(ValueError, match="never controls"):
        collect(protocol, run, store, tmp_path / "bad")


def test_secret_lost_create_ack_recovers_identity_without_second_create(tmp_path):
    _, run, config, _ = admission(tmp_path)
    backend = SimpleNamespace(config=config, core=Mock())
    value = "fake-key-for-ack-test"
    private = tmp_path.parent / (tmp_path.name + "-private")
    private.write_text(value)
    private.chmod(0o600)
    name = f"rtv-model-{run['run_sha256'][:24]}-mini"
    backend.core.create_namespaced_secret.side_effect = TimeoutError("lost ack")
    backend.core.read_namespaced_secret.return_value = SimpleNamespace(
        data=json.dumps(
            {
                "metadata": {
                    "name": name,
                    "uid": "original",
                    "labels": {
                        credentials.LABEL: run["run_sha256"][:63],
                    },
                },
                "immutable": True,
                "data": {"credential": base64.b64encode(value.encode()).decode()},
            }
        )
    )
    try:
        ref = credentials.install_secret(backend, run["run_sha256"], "mini", private)
        assert ref == {"name": name, "uid": "original"}
        backend.core.create_namespaced_secret.assert_called_once()
    finally:
        private.unlink()


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1, 0, 1.98, True, 9])
def test_invalid_approval_never_admits_a_pilot(tmp_path, budget):
    _, run, config, _ = admission(tmp_path)
    with pytest.raises(ValueError):
        credentials.authorize(run, budget, config.model_access["secrets"])


@pytest.mark.parametrize(
    "count,budget,allowed", [(15, 0.992, True), (15, 0.9919, False), (16, 1, False)]
)
def test_fresh_sixteenth_request_fits_exact_budget_but_seventeenth_is_rejected(
    count, budget, allowed
):
    from rtv.runtime.store import Store

    total = 0
    for _ in range(count):
        total += 0.062  # Reproduce PostgreSQL float8 accumulation, not Python 3.12 sum().
    current = {
        "trial_id": "trial",
        "execution_id": "exec",
        "spec": {"api_call_limit": 16, "api_budget_usd": budget},
    }
    connection = Mock()
    connection.execute.side_effect = [
        Mock(fetchone=Mock(return_value={"active": True})),
        Mock(fetchone=Mock(return_value={"n": count, "total": total})),
        Mock(fetchone=Mock(return_value={"admitted": True})),
        Mock(),
    ]
    store = Store.__new__(Store)
    store.owned = Mock(return_value=nullcontext((connection, current)))
    store._event = Mock()
    if allowed:
        assert store.begin_model_call(current, "parent", count + 1, 0.062)
        assert connection.execute.call_count == 4
    else:
        with pytest.raises(ValueError, match="budget exhausted"):
            store.begin_model_call(current, "parent", count + 1, 0.062)
        assert connection.execute.call_count == 2
