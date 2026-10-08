import ast
import asyncio
import json
import os
import runpy
import shlex
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rtv import harbor_probe as probe
from rtv.cli import main

TASK = Path(__file__).resolve().parents[1] / "examples/tasks/positive-sum"
CHECK = runpy.run_path(str(TASK / "tests/check.py"))["check"]


def test_task_verifier_rejects_original_and_partial_but_accepts_completed_code(tmp_path):
    candidate = tmp_path / "positive_sum.py"
    # Avoid Python's timestamp-based bytecode cache when replacing small fixture files.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        for content in [(TASK / "environment/positive_sum.py").read_text(), probe.PARTIAL_SOURCE]:
            candidate.write_text(content)
            with pytest.raises(AssertionError):
                CHECK(tmp_path)
        candidate.write_text(probe.FINAL_SOURCE)
        CHECK(tmp_path)
    finally:
        sys.dont_write_bytecode = previous


def test_doctor_reports_missing_runtime_without_creating_output(monkeypatch, capsys):
    def missing(*args, **kwargs):
        raise FileNotFoundError("Docker unavailable")

    monkeypatch.setattr(probe, "_docker", missing)
    assert main(["harbor-doctor", "--task", str(TASK)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert not report["ready"]
    assert not report["checks"]["docker"]["ok"]


def test_preflight_failure_leaves_no_partial_run(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "doctor", lambda _: {"ready": False})
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="preflight"):
        asyncio.run(probe.run_smoke(TASK, output))
    assert not output.exists()


def test_workspace_digest_covers_permissions_and_rejects_links(tmp_path):
    with pytest.raises(ValueError, match="real directory"):
        probe.tree_digest(tmp_path / "missing")
    path = tmp_path / "file"
    path.write_text("hello")
    path.chmod(0o644)
    before = probe.tree_digest(tmp_path)
    path.chmod(0o755)
    assert probe.tree_digest(tmp_path) != before
    (tmp_path / "alias").symlink_to(path)
    with pytest.raises(ValueError, match="Symbolic"):
        probe.tree_digest(tmp_path)


@pytest.fixture
def fake_harbor(monkeypatch, tmp_path):
    if sys.version_info < (3, 12):
        pytest.skip("Harbor integration requires Python 3.12+")
    docker_module = pytest.importorskip("harbor.environments.docker.docker")
    verifier_module = pytest.importorskip("harbor.verifier.verifier")
    from harbor.models.task.task import Task

    Task(TASK)  # Validate against the real pinned SDK, independently of mocked transport.
    state = {
        "environments": {},
        "stop_fails": False,
        "leak": False,
        "corrupt": False,
        "verification_fails": False,
        "cancel_export": False,
    }

    class Environment:
        def __init__(self, *, session_id, trial_paths, task_env_config, mounts, **kwargs):
            self.session_id = session_id
            self.trial_paths = trial_paths
            self.task_env_config = task_env_config
            self.mounts = mounts
            self.root = tmp_path / session_id
            self.root.mkdir()
            self.running = False
            self.deleted = False
            state["environments"][session_id] = self

        async def start(self, **kwargs):
            self.running = True
            shutil.copyfile(TASK / "environment/positive_sum.py", self.root / "positive_sum.py")

        async def stop(self, **kwargs):
            self.deleted = not state["leak"]
            self.running = False

        async def exec(self, *, command, **kwargs):
            assert self.running
            if command.startswith("python -c "):
                syntax = ast.parse(shlex.split(command)[2])
                content = ast.literal_eval(syntax.body[-1].value.args[0])
                (self.root / "positive_sum.py").write_text(content)
            elif command == "rm -rf /workspace && mkdir /workspace":
                for file in self.root.iterdir():
                    file.unlink()
            else:
                raise AssertionError(f"Unexpected command: {command}")
            return SimpleNamespace(return_code=0, stderr="")

        async def download_dir(self, source_dir, target_dir):
            if state["cancel_export"]:
                raise asyncio.CancelledError()
            shutil.copytree(self.root, target_dir, dirs_exist_ok=True)
            if self.trial_paths.trial_dir.name == "target" and state["corrupt"]:
                (Path(target_dir) / "positive_sum.py").write_text("corrupted")

        async def upload_dir(self, source_dir, target_dir):
            shutil.copytree(source_dir, self.root, dirs_exist_ok=True)

        async def download_file(self, source_path, target_path):
            shutil.copyfile(self.root / "positive_sum.py", target_path)

    class Verifier:
        def __init__(self, task, paths, env):
            self.env = env

        async def verify(self):
            namespace = {}
            exec((self.env.root / "positive_sum.py").read_text(), namespace)
            func = namespace["positive_sum"]
            passed = func([]) == 0 and func([1, -2, 3]) == 4
            return SimpleNamespace(
                rewards={"reward": float(passed and not state["verification_fails"])}
            )

    def docker(*args, **kwargs):
        if args[0] == "ps":
            project = next(
                arg for arg in args if arg.startswith("label=com.docker.compose.project=")
            )
            session = project.rsplit("=", 1)[1]
            env = state["environments"][session]
            if env.deleted:
                return ""
            if "label=com.docker.compose.service=main" in args:
                return session
            return session + " " + session + "-sidecar"
        if args[0] == "inspect" and args[2] == "{{.Image}}":
            return (
                "sha256:fixture-sidecar"
                if args[-1].endswith("-sidecar")
                else "sha256:fixture-image"
            )
        env = state["environments"][args[-1]]
        if args[0] == "stop":
            env.running = state["stop_fails"]
            return env.session_id
        if args[0] == "inspect":
            return json.dumps({"Running": env.running})
        raise AssertionError(args)

    monkeypatch.setattr(docker_module, "DockerEnvironment", Environment)
    monkeypatch.setattr(verifier_module, "Verifier", Verifier)
    monkeypatch.setattr(probe, "_docker", docker)
    monkeypatch.setattr(probe, "doctor", lambda _: {"ready": True})
    return state


def test_scripted_protocol_preserves_source_and_verifies_target(fake_harbor, tmp_path):
    output = tmp_path / "run"
    report = asyncio.run(probe.run_smoke(TASK, output))
    assert report["status"] == "passed", report
    assert report["model_calls"] == 0
    source, target = fake_harbor["environments"].values()
    assert source.task_env_config.docker_image is None
    assert target.task_env_config.docker_image == report["source_image_id"]
    assert report["target_image_id"] == report["source_image_id"]
    assert {mount["target"]: mount["source"] for mount in target.mounts} == {
        "/logs/agent": str(output / "target/agent"),
        "/logs/verifier": str(output / "target/verifier"),
    }
    assert all(len(images) == 2 for images in report["container_images"].values())
    assert report["verifier_before"] == {"reward": 0}
    assert report["verifier_after"] == {"reward": 1}
    assert (output / "source-after.py").read_text() == probe.PARTIAL_SOURCE
    assert all(env.deleted for env in fake_harbor["environments"].values())
    assert json.loads((output / "report.json").read_text())["status"] == "passed"
    with pytest.raises(FileExistsError):
        asyncio.run(probe.run_smoke(TASK, output))


@pytest.mark.parametrize("failure", ["stop_fails", "corrupt", "verification_fails", "leak"])
def test_failures_do_not_produce_success_and_cleanup_is_attempted(fake_harbor, tmp_path, failure):
    fake_harbor[failure] = True
    output = tmp_path / "run"
    report = asyncio.run(probe.run_smoke(TASK, output))
    assert report["status"] == "failed", report
    assert (output / "report.json").is_file()
    if failure == "stop_fails":
        assert "Source is not stopped" in report["error"]
        assert not list((output / "checkpoints").iterdir())
    elif failure == "corrupt":
        assert "digest differs" in report["error"]
    elif failure == "verification_fails":
        assert "Final fixture verification failed" in report["error"]
    if failure == "leak":
        assert report["cleanup_errors"]
    else:
        assert all(env.deleted for env in fake_harbor["environments"].values())


def test_cancellation_is_reported_and_cleanup_runs(fake_harbor, tmp_path):
    fake_harbor["cancel_export"] = True
    output = tmp_path / "interrupted"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(probe.run_smoke(TASK, output))
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "interrupted"
    assert all(env.deleted for env in fake_harbor["environments"].values())


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RTV_RUN_DOCKER") != "1", reason="Opt in to local Docker execution")
def test_real_harbor_docker_roundtrip(tmp_path):
    report = asyncio.run(probe.run_smoke(TASK, tmp_path / "real"))
    assert report["status"] == "passed", report
