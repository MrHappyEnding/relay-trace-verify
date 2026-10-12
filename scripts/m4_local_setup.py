"""Provision only the named local k3d development cluster, without paid calls."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONTROL = REPO / "work/m4-control"
DATA = REPO / "work/m4-data"


def call(*args, **kwargs):
    return subprocess.run(args, cwd=REPO, check=True, text=True, **kwargs)


def main(build):
    CONTROL.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    clusters = json.loads(call("k3d", "cluster", "list", "-o", "json", capture_output=True).stdout)
    if not any(c["name"] == "rtv-m4" for c in clusters):
        call(
            "k3d",
            "cluster",
            "create",
            "rtv-m4",
            "--servers",
            "1",
            "--agents",
            "2",
            "--no-lb",
            "--image",
            "rancher/k3s:v1.31.5-k3s1",
            "--api-port",
            "127.0.0.1:6554",
            "--volume",
            f"{DATA}:{DATA}@all",
            "--k3s-node-label",
            "rtv.dev/executor=true@agent:*",
            "--k3s-arg",
            "--disable=traefik,servicelb,metrics-server@server:0",
            "--kubeconfig-update-default=false",
            "--kubeconfig-switch-context=false",
            "--wait",
            "--timeout",
            "180s",
        )
    # Ensure an existing cluster really mounts this repository's artifact volume.
    for name in ("server-0", "agent-0", "agent-1"):
        mounts = json.loads(
            call(
                "docker",
                "inspect",
                "--format",
                "{{json .Mounts}}",
                "k3d-rtv-m4-" + name,
                capture_output=True,
            ).stdout
        )
        if not any(m["Source"] == str(DATA) and m["Destination"] == str(DATA) for m in mounts):
            raise ValueError("Existing rtv-m4 cluster has a different shared volume")
    kubeconfig = CONTROL / "kubeconfig"
    kubeconfig.write_text(call("k3d", "kubeconfig", "get", "rtv-m4", capture_output=True).stdout)
    kubeconfig.chmod(0o600)
    if build:
        call(
            "docker",
            "build",
            "-f",
            "infra/m4/Dockerfile",
            "--provenance=false",
            "-t",
            "rtv-m4-runner:dev",
            ".",
        )
    image = json.loads(
        call("docker", "image", "inspect", "rtv-m4-runner:dev", capture_output=True).stdout
    )[0]
    platform = "linux/" + image["Architecture"]
    bootstrap = CONTROL / "bootstrap-images.tar"
    call(
        "docker",
        "save",
        "--platform",
        platform,
        "--output",
        str(bootstrap),
        "rtv-m4-runner:dev",
        "postgres:17.6-bookworm",
    )
    for node in ("server-0", "agent-0", "agent-1"):
        with bootstrap.open("rb") as stream:
            call(
                "docker",
                "exec",
                "-i",
                "k3d-rtv-m4-" + node,
                "ctr",
                "-n",
                "k8s.io",
                "images",
                "import",
                "--platform",
                platform,
                "--digests",
                "-",
                stdin=stream,
            )
    resolved = {}
    for node in ("server-0", "agent-0", "agent-1"):
        for ref in ("rtv-m4-runner:dev", "postgres:17.6-bookworm"):
            status = json.loads(
                call(
                    "docker",
                    "exec",
                    "k3d-rtv-m4-" + node,
                    "crictl",
                    "inspecti",
                    ref,
                    capture_output=True,
                ).stdout
            )["status"]
            platform_digest = status["repoDigests"][0].split("@", 1)[1]
            pinned = "docker.io/library/" + ref.split(":", 1)[0] + "@" + platform_digest
            if ref in resolved and resolved[ref] != pinned:
                raise ValueError("Nodes imported different image identities")
            resolved[ref] = pinned
            call(
                "docker",
                "exec",
                "k3d-rtv-m4-" + node,
                "ctr",
                "-n",
                "k8s.io",
                "images",
                "tag",
                "--force",
                "docker.io/library/" + ref,
                pinned,
            )
    digest = resolved["rtv-m4-runner:dev"]
    kubectl = ["kubectl", "--kubeconfig", str(kubeconfig), "--context", "k3d-rtv-m4"]
    old = call(
        *kubectl,
        "get",
        "namespace",
        "rtv-m4",
        "--ignore-not-found",
        "-o",
        "json",
        capture_output=True,
    ).stdout
    if old and json.loads(old)["metadata"].get("labels", {}).get("rtv.dev/managed-by") != "rtv":
        raise ValueError("Refusing to modify a namespace not owned by RTV")
    import yaml

    services = list(yaml.safe_load_all((REPO / "infra/m4/services.yaml").read_text()))
    for resource in services:
        if resource["kind"] == "StatefulSet":
            resource["spec"]["template"]["spec"]["containers"][0]["image"] = resolved[
                "postgres:17.6-bookworm"
            ]
    call(
        *kubectl,
        "apply",
        "-f",
        "-",
        input=json.dumps({"apiVersion": "v1", "kind": "List", "items": services}),
    )
    call(
        *kubectl, "-n", "rtv-m4", "rollout", "status", "statefulset/rtv-postgres", "--timeout=120s"
    )
    archive = DATA / "images.tar"
    temporary = DATA / "images.pending.tar"
    call(
        "docker",
        "save",
        "--output",
        str(temporary),
        "rtv-m1-fixture",
        "harbor-prebuilt:harbor-docker-egress-control-sidecar--e9f39ce1643ee690",
    )
    with temporary.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    archive = DATA / ("images-" + checksum[:16] + ".tar")
    if archive.exists():
        temporary.unlink()
    else:
        temporary.replace(archive)
    config = dict(
        kubeconfig=str(kubeconfig),
        context="k3d-rtv-m4",
        namespace="rtv-m4",
        shared_path=str(DATA),
        runner_image=digest,
        images_archive=str(archive),
        images_sha256=checksum,
        pod_database_url="postgresql://rtv:rtv-local-cluster@rtv-postgres.rtv-m4.svc:5432/rtv",
        control_database_url="postgresql://rtv:rtv-local-cluster@127.0.0.1:55434/rtv",
    )
    path = CONTROL / "config.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    path.chmod(0o600)
    print(
        json.dumps(
            {
                "config": str(path),
                "runner_image": digest,
                "images_sha256": checksum,
                "model_calls": 0,
                "next": "Start PostgreSQL port-forward on 55434 and Temporal on 7233",
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true")
    main(parser.parse_args().build)
