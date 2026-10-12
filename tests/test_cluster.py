"""M4 pure contracts and mocked Kubernetes faults; no cluster/model required."""

import json
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
