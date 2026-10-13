"""One development task: real offline negative/oracle controls, zero model calls."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from rtv.benchmark.protocol import load
from rtv.benchmark.source import audit, catalog_for
from rtv.benchmark.verification import control_result


def command(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main(args):
    plan = load(args.plan)
    audit(plan, catalog_for(args.plan), args.upstream)
    task = next(t for t in plan["tasks"] if t["instance_id"] == args.task)
    if task["split"] != args.split:
        raise ValueError("Task does not belong to the explicitly requested split")
    args.output.mkdir(parents=True, exist_ok=False)
    source = args.upstream / task["harbor_path"]
    build = args.output / "build"
    build.mkdir()
    shell = (source / "tests/test.sh").read_text()
    match = re.search(r"cat > parser.py <<EOF\n(.*?)\nEOF", shell, re.S)
    if not match:
        raise ValueError("Pinned verifier parser layout changed")
    (build / "parser.py").write_text(match[1] + "\n")
    dockerfile = (source / "environment/Dockerfile").read_text()
    dockerfile, count = re.subn(
        r"^FROM \S+", "FROM " + task["image"]["pinned"], dockerfile, flags=re.M
    )
    if count != 1:
        raise ValueError("Expected one pinned base image")
    shutil.copy2(source / "tests/config.json", build / "config.json")
    helper = Path(__file__).resolve().parents[1] / "infra/m4/offline-verifier"
    shutil.copytree(helper, build / "offline-http")
    dockerfile += (
        "\nCOPY parser.py /parser.py\n"
        "RUN /root/.local/bin/uv lock --script /parser.py --python 3.11 "
        "&& /root/.local/bin/uv sync --locked --script /parser.py --python 3.11\n"
        "COPY offline-http /opt/rtv-offline-http\n"
        "RUN --mount=type=bind,source=config.json,target=/rtv-build-config.json "
        "PYTHONPATH=/opt/rtv-offline-http RTV_CACHE_RECORD=1 "
        "/root/.cache/uv/environments-v2/parser-*/bin/python /opt/rtv-offline-http/warm.py\n"
        "ENV PYTHONPATH=/opt/rtv-offline-http\n"
        "ENV UV_OFFLINE=1 PIP_NO_INDEX=1 UV_PYTHON_DOWNLOADS=never\n"
    )
    (build / "Dockerfile").write_text(dockerfile)
    name = "rtv-benchmark-preflight:" + plan["protocol_sha256"][:12] + "-" + args.task
    report = {
        "mode": "real-container-oracle-controls",
        "paid_api_calls": 0,
        "protocol_sha256": plan["protocol_sha256"],
        "task": task["instance_id"],
        "platform": task["image"]["platform"],
        "base_image": task["image"]["pinned"],
        "scope": "verifier/environment controls only; no policy tuning or agent efficacy evidence",
        "split": args.split,
        "status": "failed",
        "controls": {},
        "cleanup": [],
        "build_dockerfile_sha256": hashlib.sha256((build / "Dockerfile").read_bytes()).hexdigest(),
    }
    started = time.monotonic()
    try:
        with (args.output / "build.log").open("w") as log:
            command(
                "docker",
                "build",
                "--platform",
                task["image"]["platform"],
                "--provenance=false",
                "-t",
                name,
                str(build),
                stdout=log,
                stderr=log,
                timeout=1800,
            )
        info = json.loads(command("docker", "image", "inspect", name, capture_output=True).stdout)[
            0
        ]
        report["verifier_image_id"] = info["Id"]
        for control in ("unmodified", "oracle"):
            cid = None
            case = args.output / control
            case.mkdir()
            try:
                cid = command(
                    "docker",
                    "create",
                    "--platform",
                    task["image"]["platform"],
                    "--name",
                    "rtv-benchmark-" + uuid4().hex,
                    "--label",
                    "rtv.dev/preflight=true",
                    "--network",
                    "none",
                    "--cpus",
                    "1",
                    "--memory",
                    "4096m",
                    "--pids-limit",
                    "512",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--entrypoint",
                    "/bin/bash",
                    info["Id"],
                    "-lc",
                    "sleep infinity",
                    capture_output=True,
                ).stdout.strip()
                command("docker", "start", cid, capture_output=True)
                head = command(
                    "docker",
                    "exec",
                    cid,
                    "git",
                    "-C",
                    "/testbed",
                    "rev-parse",
                    "HEAD",
                    capture_output=True,
                ).stdout.strip()
                trees = command(
                    "docker",
                    "exec",
                    cid,
                    "git",
                    "-C",
                    "/testbed",
                    "rev-parse",
                    "HEAD^{tree}",
                    task["base_commit"] + "^{tree}",
                    capture_output=True,
                ).stdout.splitlines()
                dirty = command(
                    "docker",
                    "exec",
                    cid,
                    "git",
                    "-C",
                    "/testbed",
                    "status",
                    "--porcelain",
                    capture_output=True,
                ).stdout.strip()
                # Official images may add an empty packaging commit on top of the base.
                if len(trees) != 2 or trees[0] != trees[1] or dirty:
                    raise ValueError("Image worktree does not match the clean frozen base tree")
                command(
                    "docker",
                    "exec",
                    cid,
                    "mkdir",
                    "-p",
                    "/logs/verifier",
                    "/tests",
                    capture_output=True,
                )
                command(
                    "docker",
                    "cp",
                    str(source / "tests") + "/.",
                    cid + ":/tests",
                    capture_output=True,
                )
                if control == "unmodified":
                    command(
                        "docker",
                        "cp",
                        cid + ":/opt/rtv-offline-http",
                        str(args.output / "offline-http"),
                        capture_output=True,
                    )
                if control == "oracle":
                    command(
                        "docker",
                        "cp",
                        str(source / "solution/solve.sh"),
                        cid + ":/tmp/oracle.sh",
                        capture_output=True,
                    )
                    with (case / "oracle.log").open("w") as log:
                        command(
                            "docker",
                            "exec",
                            "-w",
                            "/testbed",
                            cid,
                            "bash",
                            "/tmp/oracle.sh",
                            stdout=log,
                            stderr=log,
                            timeout=180,
                        )
                with (case / "verifier.log").open("w") as log:
                    completed = subprocess.run(
                        ["docker", "exec", cid, "bash", "/tests/test.sh"],
                        stdout=log,
                        stderr=log,
                        timeout=600,
                    )
                command("docker", "cp", cid + ":/logs/verifier/.", str(case), capture_output=True)
                expected = 1 if control == "oracle" else 0
                report["controls"][control] = {
                    **control_result(case, task["instance_id"], expected, completed.returncode),
                    "base_commit": task["base_commit"],
                    "image_head": head,
                    "base_tree": trees[0],
                    "network": "none",
                }
                command(
                    "docker",
                    "cp",
                    cid + ":/parser.py.lock",
                    str(args.output / "parser.py.lock"),
                    capture_output=True,
                )
                report["parser_lock_captured"] = True
            finally:
                if cid:
                    removed = subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
                    report["cleanup"].append({"container": cid, "removed": removed.returncode == 0})
                    if removed.returncode:
                        raise RuntimeError("Preflight container cleanup failed")
        report["status"] = "passed"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["artifact_sha256"] = {
            p.relative_to(args.output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in args.output.rglob("*")
            if p.is_file()
        }
        (args.output / "preflight.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "--plan", type=Path, default=Path("configs/benchmarks/swebench-verified-v1.json")
    )
    p.add_argument("--split", choices=["pilot", "evaluation"], default="pilot")
    p.add_argument("--upstream", required=True, type=Path)
    p.add_argument("--task", required=True)
    p.add_argument("--output", required=True, type=Path)
    main(p.parse_args())
