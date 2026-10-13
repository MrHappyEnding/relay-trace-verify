"""One Job, one daemon, one physical execution; never restart an agent turn."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from uuid import UUID

from rtv.cluster.store import ClusterStore
from rtv.runtime.config import Settings, execution_dir


def main(trial_id, execution_id):
    settings = Settings.load()
    if settings.schema != "rtv4" or os.environ.get("RTV_CLUSTER_JOB") != "1":
        raise ValueError("Cluster runner requires an admitted M4 Job")
    store = ClusterStore(settings)
    allocation = store.allocation(execution_id)
    row = store.get(trial_id)
    if (
        allocation is None
        or allocation["state"] == "released"
        or row["execution_id"] != execution_id
    ):
        raise ValueError("Execution has no live resource allocation")
    from rtv.cluster.credentials import supervisor_environment
    from rtv.cluster.repository import validate_spec

    pool = json.loads(os.environ["RTV_CLUSTER_POOL"])
    validate_spec(pool, row["spec"])
    child_env = supervisor_environment(pool, settings.root, row)
    archive = Path(os.environ["RTV_IMAGES_ARCHIVE"])
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != os.environ["RTV_IMAGES_SHA256"]:
            raise ValueError("Prepared image archive digest changed")
    with Path("/tmp/dockerd.log").open("wb") as log:
        daemon = subprocess.Popen(
            [
                "dockerd",
                "--host=unix:///var/run/docker.sock",
                "--feature=containerd-snapshotter=true",
                "--storage-driver=overlayfs",
                "--log-level=error",
                "--mtu=1400",
            ],
            stdout=log,
            stderr=log,
        )
    child = None
    output = execution_dir(settings, row)
    output.mkdir(parents=True, exist_ok=True)

    def stop(signum, frame):
        if child and child.poll() is None:
            child.terminate()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        end = time.monotonic() + 45
        while time.monotonic() < end:
            if daemon.poll() is not None:
                raise RuntimeError(
                    "Executor daemon exited: " + Path("/tmp/dockerd.log").read_text()[-6000:]
                )
            ready = subprocess.run(
                ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3
            )
            if ready.returncode == 0:
                break
            time.sleep(0.3)
        else:
            raise TimeoutError("Executor daemon did not become ready")
        subprocess.run(
            ["docker", "load", "--input", str(archive)],
            check=True,
            timeout=pool["image_load_seconds"],
            stdout=subprocess.DEVNULL,
        )
        declared = row["spec"].get("task_contract")
        if declared:
            for image in {declared["agent_image_id"], declared["verifier_image_id"]}:
                info = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
                if info["Id"] != image or (info["Os"], info["Architecture"]) != ("linux", "amd64"):
                    raise ValueError("Loaded repository image does not match its contract")
        with (output / "supervisor.log").open("ab") as log:
            child = subprocess.Popen(
                [sys.executable, "-m", "rtv.runtime.supervisor", trial_id, execution_id],
                stdout=log,
                stderr=log,
                env=child_env,
            )
        child.wait()
        if child.returncode:
            raise RuntimeError("Supervisor process failed")
    finally:
        if child and child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        (output / "dockerd.log").write_bytes(Path("/tmp/dockerd.log").read_bytes())
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait(timeout=5)


def record_startup_failure(trial_id, execution_id, error):
    from rtv.runtime.files import atomic_json

    UUID(trial_id)
    UUID(execution_id)
    output = Settings.load().root / trial_id / "executions" / execution_id
    output.mkdir(parents=True, exist_ok=True)
    # Exception text and locals can contain credentials. Persist only stack locations.
    atomic_json(
        output / "runner-error.json",
        {
            "error_type": type(error).__name__,
            "frames": [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(error.__traceback__)
            ],
        },
    )


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except Exception as error:
        record_startup_failure(*sys.argv[1:], error)
        raise
