"""M4 pure contracts and mocked Kubernetes faults; no cluster/model required."""

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rtv.cluster.config import ClusterConfig
from rtv.cluster.kubernetes import Kubernetes, job_manifest
from rtv.cluster.report import quantile, trial_metrics
from rtv.runtime.config import Settings


def configuration(tmp_path):
    return ClusterConfig(
        kubeconfig=str(tmp_path / "kubeconfig"),
        context="k3d-rtv-m4",
        namespace="rtv-m4",
        shared_path=str(tmp_path),
        runner_image="local/runner@sha256:" + "a" * 64,
        images_archive=str(tmp_path / "images.tar"),
        images_sha256="b" * 64,
        pod_database_url="postgresql://pod-secret/db",
        control_database_url="postgresql://control-secret/db",
    )


def test_explicit_schema_and_config(tmp_path):
    config = configuration(tmp_path)
    settings = Settings(tmp_path / "run", "", "", schema="rtv4")
    config.validate_root(settings)
    assert settings.queue != replace(settings, schema="rtv3").queue
    assert "secret" not in json.dumps(config.frozen())
    with pytest.raises(ValueError):
        replace(settings, schema="injected;")
    with pytest.raises(ValueError):
        config.validate_root(replace(settings, root=tmp_path.parent))
    with pytest.raises(ValueError):
        replace(config, runner_image="runner:latest")
    with pytest.raises(ValueError):
        replace(config, images_archive="/outside/images.tar")
    with pytest.raises(ValueError):
        replace(config, images_archive=str(tmp_path / ".." / "outside.tar"))


def test_retry_contract_and_recovery_node_frozen(tmp_path):
    config = configuration(tmp_path)
    settings = Settings(tmp_path / "run", "", "", schema="rtv4")
    row = dict(
        execution_id="execution",
        trial_id="trial",
        epoch=3,
        spec_hash="spec",
        deadline=datetime.now(UTC) + timedelta(seconds=120),
    )
    allocation = dict(job_name="stable", avoid_nodes=["failed-node"])
    manifest = job_manifest(settings, config, row, allocation)
    spec = manifest["spec"]
    pod = spec["template"]["spec"]
    assert spec["backoffLimit"] == 0 and 1 <= spec["activeDeadlineSeconds"] <= 120
    assert pod["restartPolicy"] == "Never" and not pod["automountServiceAccountToken"]
    assert pod["containers"][0]["resources"]["limits"]["memory"] == "1536Mi"
    assert "docker.sock" not in json.dumps(pod["volumes"])
    assert "OPENAI_API_KEY" not in json.dumps(manifest)
    terms = pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]
    assert terms["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["failed-node"]
    assert job_manifest(settings, config, row, allocation)["metadata"] == manifest["metadata"]
    changed = job_manifest(settings, config, row, {**allocation, "avoid_nodes": []})
    assert changed["metadata"]["annotations"] != manifest["metadata"]["annotations"]


def backend():
    pytest.importorskip("kubernetes")
    api = Kubernetes.__new__(Kubernetes)
    api.config = SimpleNamespace(namespace="rtv-m4")
    api.batch = Mock()
    return api


def test_lost_create_ack_adopts_same_job_without_second_create():
    api = backend()
    manifest = {"metadata": {"name": "stable", "annotations": {"rtv.dev/contract": "same"}}}
    stored = {**manifest, "spec": {}}
    api.job = Mock(side_effect=[None, stored, stored])
    api.batch.create_namespaced_job.side_effect = TimeoutError("ack lost")
    with pytest.raises(TimeoutError):
        api.ensure(manifest)
    assert api.ensure(manifest) == stored
    api.batch.create_namespaced_job.assert_called_once()


def test_name_collision_does_not_adopt():
    api = backend()
    api.job = Mock(return_value={"metadata": {"annotations": {"rtv.dev/contract": "foreign"}}})
    with pytest.raises(ValueError):
        api.ensure({"metadata": {"name": "stable", "annotations": {"rtv.dev/contract": "ours"}}})
    api.batch.create_namespaced_job.assert_not_called()


def test_delete_uses_uid_and_waits_for_pods():
    api = backend()
    job = {
        "metadata": {"uid": "uid1", "labels": {"rtv.dev/pool": "pool", "rtv.dev/execution": "exec"}}
    }
    allocation = dict(job_name="stable", job_uid="uid1", execution_id="exec")
    api.job = Mock(side_effect=[job, None])
    api.pods = Mock(return_value=[{"metadata": {"deletionTimestamp": "now"}}])
    assert not api.remove(allocation, "pool")
    body = api.batch.delete_namespaced_job.call_args.kwargs["body"]
    assert body["preconditions"] == {"uid": "uid1"}
    assert body["propagationPolicy"] == "Foreground"
    api.job = Mock(return_value={**job, "metadata": {**job["metadata"], "uid": "uid2"}})
    with pytest.raises(ValueError):
        api.remove(allocation, "pool")
    assert api.batch.delete_namespaced_job.call_count == 1


def test_api_failure_does_not_claim_cleanup():
    api = backend()
    api.job = Mock(side_effect=ConnectionError("offline"))
    with pytest.raises(ConnectionError):
        api.remove(dict(job_name="stable"), "pool")
    api.batch.delete_namespaced_job.assert_not_called()


def test_report_incomplete_does_not_reuse_previous_stage_finish():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    row = dict(
        trial_id="trial",
        execution_id="new",
        status="running",
        result=None,
        cleanup="pending",
        created_at=start,
    )

    def event(kind, seconds, execution):
        return dict(
            kind=kind, at=start + timedelta(seconds=seconds), payload={"execution_id": execution}
        )

    events = [
        event("execution.claimed", 2, "old"),
        event("trial.finished", 5, "old"),
        event("execution.recovered", 10, "new"),
        event("execution.claimed", 15, "new"),
    ]
    metrics = trial_metrics(row, events)
    assert not metrics["terminal"] and metrics["elapsed_seconds"] is None
    assert metrics["queue_seconds"] == 2 and metrics["recovery_to_claim_seconds"] == [5]
    row.update(status="failed", result={"reason": "lost"})
    events.append(event("trial.finished", 20, "new"))
    metrics = trial_metrics(row, events)
    assert metrics["terminal"] and metrics["reason"] == "lost" and metrics["elapsed_seconds"] == 20
    assert quantile([], 0.5) is None and quantile([1, 3], 0.5) == 2


def test_repository_pool_reserves_engine_overhead_and_disk(tmp_path):
    base = configuration(tmp_path)
    with pytest.raises(ValueError, match="Repository pool"):
        replace(base, repository_contract_sha256="d" * 64)
    config = replace(
        base,
        memory_mb=5120,
        cpu_millis=2000,
        engine_disk_gib=16,
        repository_contract_sha256="d" * 64,
        image_load_seconds=300,
    )
    settings = Settings(tmp_path / "run", "", "", schema="rtv4")
    row = dict(
        execution_id="execution",
        trial_id="trial",
        epoch=1,
        spec_hash="spec",
        deadline=datetime.now(UTC) + timedelta(seconds=1800),
    )
    pod = job_manifest(settings, config, row, {"job_name": "job"})["spec"]["template"]["spec"]
    executor = pod["containers"][0]
    assert executor["resources"]["requests"] == {
        "cpu": "2000m",
        "memory": "5120Mi",
        "ephemeral-storage": "17Gi",
    }
    assert pod["volumes"][1]["emptyDir"]["sizeLimit"] == "16Gi"
    pool = json.loads(next(e["value"] for e in executor["env"] if e["name"] == "RTV_CLUSTER_POOL"))
    assert pool["repository_contract_sha256"] == "d" * 64
    assert "pod_database_url" not in pool and pool["image_load_seconds"] == 300


def test_repository_delivery_is_bound_and_cannot_escape_shared_root(tmp_path):
    from rtv.benchmark.protocol import digest
    from rtv.cluster.repository import validate_spec
    from rtv.harbor_probe import tree_digest

    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("public issue")
    body = dict(
        schema_version=1,
        kind="swebench",
        instance_id="a__b-1",
        split="pilot",
        workspace="/testbed",
        workspace_excludes=[".DS_Store"],
        platform="linux/amd64",
        task_directory=str(task),
        task_digest=tree_digest(task),
        protocol_sha256="a" * 64,
        base_commit="b" * 40,
        agent_image_id="sha256:" + "c" * 64,
        verifier_image_id="sha256:" + "d" * 64,
        verifier_timeout_seconds=600,
    )
    declared = {**body, "contract_sha256": digest(body)}
    config = replace(
        configuration(tmp_path),
        memory_mb=5120,
        cpu_millis=2000,
        engine_disk_gib=16,
        repository_contract_sha256=declared["contract_sha256"],
    )
    spec = dict(
        adapter="fixture",
        task_contract=declared,
        task_digest=declared["task_digest"],
        image_id=declared["agent_image_id"],
    )
    validate_spec(config.frozen(), spec)
    with pytest.raises(ValueError, match="not admitted"):
        validate_spec(configuration(tmp_path).frozen(), spec)
    with pytest.raises(ValueError, match="credential"):
        validate_spec(config.frozen(), {**spec, "adapter": "mini"})
    with pytest.raises(ValueError, match="shared volume"):
        validate_spec({**config.frozen(), "shared_path": str(tmp_path / "elsewhere")}, spec)
    (task / "instruction.md").write_text("changed")
    with pytest.raises(ValueError, match="task changed"):
        validate_spec(config.frozen(), spec)


@pytest.mark.parametrize("cancel", [False, True])
def test_bad_task_cannot_launch_or_block_cancellation(tmp_path, monkeypatch, cancel):
    pytest.importorskip("psycopg")
    import rtv.cluster.control as control
    import rtv.cluster.repository as repository
    import rtv.runtime.control as local
    import rtv.runtime.relay_store as relay

    config = configuration(tmp_path)
    settings = Settings(tmp_path / "run", "", "", schema="rtv4")
    row = dict(
        trial_id="trial",
        execution_id="exec",
        status="queued",
        cancel_requested=cancel,
        deadline=datetime.now(UTC) + timedelta(seconds=300),
        spec={"adapter": "fixture", "model": None},
    )
    store, backend, relays = Mock(), Mock(), Mock()
    store.get.return_value = row
    store.executions.return_value = []
    store.allocation.return_value = None
    store.reconcile_lock.return_value = nullcontext(True)
    relays.pending_handoff.return_value = None
    invalid = Mock(side_effect=ValueError("delivered task was modified"))
    monkeypatch.setattr(control.ClusterConfig, "load", lambda: config)
    monkeypatch.setattr(control, "ClusterStore", lambda _: store)
    monkeypatch.setattr(control, "Kubernetes", lambda _: backend)
    monkeypatch.setattr(relay, "RelayStore", lambda _: relays)
    monkeypatch.setattr(local, "summary", lambda r: r)
    monkeypatch.setattr(repository, "validate_spec", invalid)
    control.reconcile(settings, "trial")
    assert store.finish.call_args.args[1]["reason"] == (
        "cancelled" if cancel else "invalid_cluster_task"
    )
    assert invalid.call_count == (0 if cancel else 1)
    store.reserve.assert_not_called()
    backend.ensure.assert_not_called()
