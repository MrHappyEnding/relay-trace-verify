"""Kubernetes owns Job/Pod lifecycle; RTV owns execution identity and admission."""

import hashlib
import json
import math
from datetime import UTC, datetime

from rtv.cluster.config import pool_label


def job_manifest(settings, config, row, allocation):
    from rtv.cluster.credentials import admitted_reference, mount_credential

    config.validate_root(settings)
    reference = admitted_reference(config.frozen(), settings.root, row)
    labels = {"rtv.dev/pool": pool_label(settings), "rtv.dev/execution": row["execution_id"]}
    deadline = max(1, math.ceil((row["deadline"] - datetime.now(UTC)).total_seconds()))
    env = {
        "RTV_RUNTIME_ROOT": str(settings.root),
        "RTV_SCHEMA": "rtv4",
        "RTV_DATABASE_URL": config.pod_database_url,
        "RTV_IMAGES_ARCHIVE": config.images_archive,
        "RTV_IMAGES_SHA256": config.images_sha256,
        "RTV_CLUSTER_JOB": "1",
        "RTV_CLUSTER_POOL": json.dumps(config.frozen(), sort_keys=True),
        "DOCKER_HOST": "unix:///var/run/docker.sock",
    }
    container = {
        "name": "executor",
        "image": config.runner_image,
        "imagePullPolicy": "Never",
        "args": [row["trial_id"], row["execution_id"]],
        "env": [{"name": k, "value": v} for k, v in env.items()]
        + [{"name": "RTV_POD_UID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}}}],
        "securityContext": {"privileged": True},
        "resources": {
            k: {
                "cpu": f"{config.cpu_millis}m",
                "memory": f"{config.memory_mb}Mi",
                "ephemeral-storage": f"{config.engine_disk_gib + 1}Gi",
            }
            for k in ("requests", "limits")
        },
        "volumeMounts": [
            {"name": "artifacts", "mountPath": config.shared_path},
            {"name": "engine", "mountPath": "/var/lib/docker"},
        ],
    }
    spec = {
        "backoffLimit": 0,
        "activeDeadlineSeconds": deadline,
        "parallelism": 1,
        "completions": 1,
        "template": {
            "metadata": {"labels": labels},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "terminationGracePeriodSeconds": 10,
                "nodeSelector": {"rtv.dev/executor": "true"},
                "affinity": {
                    "podAntiAffinity": {
                        "preferredDuringSchedulingIgnoredDuringExecution": [
                            {
                                "weight": 100,
                                "podAffinityTerm": {
                                    "topologyKey": "kubernetes.io/hostname",
                                    "labelSelector": {
                                        "matchLabels": {"rtv.dev/pool": pool_label(settings)}
                                    },
                                },
                            }
                        ]
                    }
                },
                "tolerations": [
                    {
                        "key": key,
                        "operator": "Exists",
                        "effect": "NoExecute",
                        "tolerationSeconds": 5,
                    }
                    for key in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable")
                ],
                "containers": [container],
                "volumes": [
                    {
                        "name": "artifacts",
                        "hostPath": {"path": config.shared_path, "type": "Directory"},
                    },
                    {"name": "engine", "emptyDir": {"sizeLimit": f"{config.engine_disk_gib}Gi"}},
                ],
            },
        },
    }
    mount_credential(spec["template"]["spec"], reference)
    if allocation.get("avoid_nodes"):
        spec["template"]["spec"]["affinity"]["nodeAffinity"] = {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {
                                "key": "kubernetes.io/hostname",
                                "operator": "NotIn",
                                "values": allocation["avoid_nodes"],
                            }
                        ]
                    }
                ]
            }
        }
    # Deadline is derived from the same absolute Trial deadline on retries; the
    # immutable contract excludes the relative seconds and secret DSN.
    contract = {
        "execution": row["execution_id"],
        "avoid_nodes": allocation.get("avoid_nodes", []),
        "epoch": row["epoch"],
        "spec": row["spec_hash"],
        "pool": config.frozen(),
        "deadline": row["deadline"].isoformat(),
    }
    digest = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": allocation["job_name"],
            "namespace": config.namespace,
            "labels": labels,
            "annotations": {"rtv.dev/contract": digest},
        },
        "spec": spec,
    }


def validate_job(existing, manifest):
    meta = existing["metadata"] if existing else {}
    expected = manifest["metadata"]
    if meta.get("annotations", {}).get("rtv.dev/contract") != expected["annotations"][
        "rtv.dev/contract"
    ] or any(meta.get("labels", {}).get(k) != v for k, v in expected.get("labels", {}).items()):
        raise ValueError("Job name collision or changed immutable contract")
    return existing


class Kubernetes:
    def __init__(self, config):
        from kubernetes import client
        from kubernetes import config as kube_config

        self.config = config
        self.client = client
        configuration = client.Configuration()
        kube_config.load_kube_config(
            config_file=config.kubeconfig,
            context=config.context,
            client_configuration=configuration,
            persist_config=False,
        )
        configuration.retries = 0
        self.api = client.ApiClient(configuration)
        self.batch = client.BatchV1Api(self.api)
        self.core = client.CoreV1Api(self.api)

    def close(self):
        self.api.close()

    def job(self, name):
        from kubernetes.client.exceptions import ApiException

        try:
            # _preload_content=False preserves Kubernetes camelCase keys.
            response = self.batch.read_namespaced_job(
                name, self.config.namespace, _request_timeout=(3, 10), _preload_content=False
            )
            return json.loads(response.data)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def pods(self, allocation):
        response = self.core.list_namespaced_pod(
            self.config.namespace,
            label_selector="job-name=" + allocation["job_name"],
            _request_timeout=(3, 10),
            _preload_content=False,
        )
        return json.loads(response.data)["items"]

    def ensure(self, manifest):
        from kubernetes.client.exceptions import ApiException

        name = manifest["metadata"]["name"]
        access = getattr(self.config, "model_access", None)
        if access:
            from rtv.cluster.credentials import check_secret

            for volume in manifest["spec"]["template"]["spec"]["volumes"]:
                if volume.get("secret"):
                    ref = next(
                        r
                        for r in access["secrets"].values()
                        if r["name"] == volume["secret"]["secretName"]
                    )
                    check_secret(self, access, ref)
        existing = self.job(name)
        if existing is None:
            try:
                self.batch.create_namespaced_job(
                    self.config.namespace, manifest, _request_timeout=(3, 15)
                )
            except ApiException as exc:
                if exc.status != 409:
                    raise
            existing = self.job(name)
        return validate_job(existing, manifest)

    def remove(self, allocation, expected_pool):
        from kubernetes.client.exceptions import ApiException

        job = self.job(allocation["job_name"])
        if job:
            meta = job["metadata"]
            if (
                meta.get("labels", {}).get("rtv.dev/pool") != expected_pool
                or meta.get("labels", {}).get("rtv.dev/execution") != allocation["execution_id"]
                or allocation["job_uid"] not in {None, meta["uid"]}
            ):
                raise ValueError("Refusing to delete a foreign/replaced Kubernetes Job")
            try:
                self.batch.delete_namespaced_job(
                    allocation["job_name"],
                    self.config.namespace,
                    body={
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "propagationPolicy": "Foreground",
                        "preconditions": {"uid": meta["uid"]},
                    },
                    _request_timeout=(3, 10),
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise
        # No force deletion: an unavailable node holds its physical reservation.
        return self.job(allocation["job_name"]) is None and not self.pods(allocation)
