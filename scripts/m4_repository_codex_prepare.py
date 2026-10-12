"""Install the existing checksum-pinned native Codex binary; never authenticate or infer."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from rtv.codex_agent import CODEX_VERSION
from rtv.harbor_probe import _docker
from rtv.runtime.config import REPO
from rtv.runtime.tasks import validate_contract


def main(args):
    declared = validate_contract(json.loads(args.contract.read_text()))
    args.output.mkdir(parents=True, exist_ok=False)
    base = declared["agent_image_id"]
    if json.loads(_docker("image", "inspect", base))[0]["Id"] != base:
        raise ValueError("Prepared base image identity changed")
    base_tag = "rtv-repository-base:" + base.split(":")[1][:20]
    _docker("tag", base, base_tag)
    installer = args.output / "install_codex.py"
    shutil.copy2(REPO / "examples/tasks/codex-session/environment/install_codex.py", installer)
    dockerfile = (
        f"FROM {base_tag}\n"
        "COPY install_codex.py /opt/rtv-install-codex.py\n"
        "RUN /root/.local/bin/uv run --offline --no-project --python 3.11 "
        "/opt/rtv-install-codex.py && codex --version\n"
        "WORKDIR /testbed\n"
    )
    (args.output / "Dockerfile").write_text(dockerfile)
    tag = "rtv-repository-codex:" + declared["contract_sha256"][:20]
    report = {"status": "failed", "model_calls": 0, "paid_api_calls": 0, "base_image_id": base}
    try:
        with (args.output / "build.log").open("w") as log:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "--platform",
                    "linux/amd64",
                    "--provenance=false",
                    "-t",
                    tag,
                    str(args.output),
                ],
                check=True,
                stdout=log,
                stderr=log,
                timeout=600,
            )
        image = json.loads(_docker("image", "inspect", tag))[0]
        version = _docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "codex",
            image["Id"],
            "--version",
        )
        if version != "codex-cli " + CODEX_VERSION:
            raise ValueError("Installed Codex version does not match the adapter")
        report.update(
            status="passed",
            agent_image_id=image["Id"],
            cli_version=version,
            installer_sha256=hashlib.sha256(installer.read_bytes()).hexdigest(),
            dockerfile_sha256=hashlib.sha256(dockerfile.encode()).hexdigest(),
        )
    finally:
        (args.output / "prepare.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
