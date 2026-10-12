"""Deliver a pinned pilot task to a shared local cluster volume, without credentials."""

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

from rtv.benchmark.protocol import digest
from rtv.harbor_probe import tree_digest
from rtv.runtime.tasks import contract, validate_contract


def validate_spec(pool, spec):
    if spec["adapter"] not in {"fixture", "mini-fixture"}:
        from rtv.cluster.credentials import validate_model_spec

        if not pool.get("model_access"):
            raise ValueError("Cluster model credential delivery is not enabled")
        validate_model_spec(spec)
    declared = contract(spec)
    if declared is None:
        if pool.get("repository_contract_sha256"):
            raise ValueError("Repository pool cannot silently run an unbound fixture")
        return
    if declared["contract_sha256"] != pool.get("repository_contract_sha256"):
        raise ValueError("Repository contract is not admitted by this pool")
    task = Path(declared["task_directory"])
    if not task.resolve().is_relative_to(Path(pool["shared_path"]).resolve()):
        raise ValueError("Repository task must be delivered on the shared volume")
    if pool["memory_mb"] < 5120 or pool["cpu_millis"] < 2000 or pool["engine_disk_gib"] < 16:
        raise ValueError("Repository resources are too small")
    if tree_digest(task) != declared["task_digest"]:
        raise ValueError("Delivered repository task changed")


def prepare(config, declared, output):
    """New output/pool identity; never modify the original task or existing pool."""
    validate_contract(declared)
    output = output.resolve()
    if not output.is_relative_to(Path(config.shared_path).resolve()):
        raise ValueError("Prepared repository must be on the configured shared volume")
    source = Path(declared["task_directory"])
    if output.is_relative_to(source.resolve()) or tree_digest(source) != declared["task_digest"]:
        raise ValueError("Invalid task source or destination")
    output.mkdir(parents=True, exist_ok=False)
    task = output / "task"
    shutil.copytree(source, task)
    if tree_digest(task) != declared["task_digest"]:
        raise ValueError("Task delivery changed its contents")
    body = {k: v for k, v in declared.items() if k != "contract_sha256"}
    body["task_directory"] = str(task)
    delivered = validate_contract({**body, "contract_sha256": digest(body)})
    archive = output / "images.tar"
    images = sorted({declared["agent_image_id"], declared["verifier_image_id"]})
    images.append("harbor-prebuilt:harbor-docker-egress-control-sidecar--e9f39ce1643ee690")
    subprocess.run(["docker", "save", "-o", str(archive), *images], check=True, timeout=600)
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    prepared = replace(
        config,
        images_archive=str(archive),
        images_sha256=checksum,
        max_jobs=1,
        cpu_millis=2000,
        memory_mb=5120,
        engine_disk_gib=16,
        image_load_seconds=300,
        repository_contract_sha256=delivered["contract_sha256"],
        model_access=None,
    )
    (output / "contract.json").write_text(json.dumps(delivered, indent=2) + "\n")
    path = output / "config.json"
    path.write_text(json.dumps(asdict(prepared), indent=2) + "\n")
    path.chmod(0o600)
    return {"config": str(path), "contract": str(output / "contract.json"), "model_calls": 0}
