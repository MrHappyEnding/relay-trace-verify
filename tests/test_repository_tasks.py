import asyncio
import json
from argparse import Namespace
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from rtv.benchmark.protocol import digest
from rtv.checkpoints import CheckpointError, CheckpointManifest
from rtv.codex_agent import CODEX_VERSION, read_session, validate_bundle
from rtv.domain import ExecutionRef
from rtv.harbor_probe import tree_digest
from rtv.runtime.handoff import target_spec
from rtv.runtime.tasks import contract, make_repository_spec, prompt, validate_contract
from rtv.storage import LocalCheckpointStore


@pytest.fixture
def declared(tmp_path):
    task = tmp_path / "definition"
    task.mkdir()
    (task / "instruction.md").write_text("Fix the public repository issue.")
    (task / "tests").mkdir()
    (task / "tests/secret.txt").write_text("HIDDEN_TEST_SENTINEL")
    (task / "solution").mkdir()
    (task / "solution/solve.sh").write_text("ORACLE_SENTINEL")
    value = {
        "schema_version": 1,
        "kind": "swebench",
        "instance_id": "test__repo-1",
        "split": "pilot",
        "workspace": "/testbed",
        "workspace_excludes": [".DS_Store"],
        "platform": "linux/amd64",
        "task_directory": str(task),
        "task_digest": tree_digest(task),
        "protocol_sha256": "a" * 64,
        "base_commit": "b" * 40,
        "agent_image_id": "sha256:" + "c" * 64,
        "verifier_image_id": "sha256:" + "d" * 64,
        "verifier_timeout_seconds": 600,
    }
    return {**value, "contract_sha256": digest(value)}


def source_spec(declared):
    return {
        "task_contract": declared,
        "task_digest": declared["task_digest"],
        "image_id": declared["agent_image_id"],
        "adapter": "codex",
        "model": "model",
    }


def test_repository_prompt_reads_only_public_instruction(declared):
    spec = source_spec(declared)
    assert prompt(spec, True) == prompt(spec, False)
    text = prompt(spec, True)
    assert "public repository issue" in text and "/testbed" in text
    assert "HIDDEN_TEST_SENTINEL" not in text and "ORACLE_SENTINEL" not in text
    assert "positive_sum.py" not in text and "return None" not in text
    (Path(declared["task_directory"]) / "tests/secret.txt").write_text("changed")
    with pytest.raises(ValueError, match="definition changed"):
        prompt(spec, True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace", "/tmp;bad"),
        ("verifier_image_id", "mutable:tag"),
        ("platform", "linux/arm64"),
        ("split", "unselected"),
    ],
)
def test_contract_cannot_silently_change_scope_or_environment(declared, field, value):
    changed = {**declared, field: value}
    with pytest.raises(ValueError, match="contract changed"):
        validate_contract(changed)
    changed["contract_sha256"] = digest(
        {k: v for k, v in changed.items() if k != "contract_sha256"}
    )
    with pytest.raises(ValueError):
        validate_contract(changed)


def test_handoff_keeps_task_contract_and_rejects_unsupported_projection(declared):
    spec = source_spec(declared)
    child = target_spec(spec, "mini", None, "summary")
    assert contract(child) == declared
    with pytest.raises(ValueError, match="ATIF"):
        target_spec(spec, "mini", None, "portable")
    child["image_id"] = "sha256:" + "e" * 64
    with pytest.raises(ValueError, match="disagrees"):
        contract(child)


def test_manifest_v3_preserves_directory_and_file_modes(declared, tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "private/empty").mkdir(parents=True)
    (workspace / "private/script").write_text("echo preserved")
    (workspace / "private/script").chmod(0o751)
    (workspace / "private").chmod(0o750)
    (workspace / "private/empty").chmod(0o777)
    store = LocalCheckpointStore(tmp_path / "checkpoints")
    ref = store.commit(
        workspace,
        ExecutionRef(uuid4()),
        event_cursor=0,
        summary="repository",
        context={"task_contract_sha256": declared["contract_sha256"]},
        preserve_directory_modes=True,
    )
    manifest = store.load(ref)
    assert manifest.schema_version == 3
    assert manifest.directory_modes == {"private": 0o750, "private/empty": 0o777}
    restored = store.restore(ref, tmp_path / "restored")
    assert tree_digest(restored) == tree_digest(workspace)
    bad = manifest.to_dict()
    bad["directory_modes"].pop("private")
    with pytest.raises(CheckpointError, match="every directory"):
        CheckpointManifest.from_dict(bad)


def test_declared_host_metadata_exclusion_does_not_hide_code_tampering(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".DS_Store").write_bytes(b"host metadata")
    (workspace / "source.py").write_text("original")
    store = LocalCheckpointStore(tmp_path / "store")
    ref = store.commit(
        workspace,
        ExecutionRef(uuid4()),
        event_cursor=0,
        summary="exclusion",
        context={"workspace_excludes": [".DS_Store"]},
        preserve_directory_modes=True,
        exclude_names=(".DS_Store",),
    )
    assert store.load(ref).excluded_names == (".DS_Store",)
    restored = store.restore(ref, tmp_path / "restored")
    assert not (restored / ".DS_Store").exists()
    (restored / ".DS_Store").write_bytes(b"different host metadata")
    assert tree_digest(restored, exclude_names=(".DS_Store",)) == tree_digest(
        workspace, exclude_names=(".DS_Store",)
    )
    snapshot = store.root / str(ref.checkpoint_id) / "files/source.py"
    snapshot.write_text("changed")
    with pytest.raises(CheckpointError, match="content mismatch"):
        store.restore(ref, tmp_path / "tampered")
    with pytest.raises(CheckpointError, match="exclusions"):
        store.commit(
            workspace,
            ExecutionRef(uuid4()),
            event_cursor=0,
            summary="forbidden",
            context={},
            preserve_directory_modes=True,
            exclude_names=("source.py",),
        )


def test_native_session_requires_matching_declared_workspace(tmp_path):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "native").mkdir()
    session = str(uuid4())
    native = tmp_path / "native/rollout.jsonl"
    native.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": session,
                    "cli_version": CODEX_VERSION,
                    "cwd": "/testbed",
                },
            }
        )
        + "\n"
    )
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cli_version": CODEX_VERSION,
                "model": "model",
                "session_id": session,
                "workspace_scope": "/testbed",
            }
        )
    )
    assert validate_bundle(tmp_path, model="model", workspace="/testbed") == native
    (tmp_path / "native/.DS_Store").write_bytes(b"host metadata")
    assert validate_bundle(tmp_path, model="model", workspace="/testbed") == native
    with pytest.raises(ValueError, match="workspace scope"):
        validate_bundle(tmp_path, model="model")
    with pytest.raises(ValueError, match="workspace path"):
        read_session(native)


def test_paid_repository_submission_requires_opt_in_and_excludes_controls(declared, tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(declared))
    args = Namespace(
        task_contract=path,
        adapter="mini",
        ttl=900,
        agent_seconds=600,
        delay=0,
        pause_after_checkpoint=False,
        staged=False,
        image=None,
        max_recoveries=0,
        allow_model_calls=False,
        allow_api_calls=False,
        api_call_limit=16,
        api_budget_usd=1,
        model=None,
        scripted_controls=None,
    )
    with pytest.raises(ValueError, match="allow-api-calls"):
        make_repository_spec(args)
    args.allow_api_calls = True
    spec = make_repository_spec(args)
    assert spec["api_call_limit"] == 16 and spec["agent_seconds"] == 600
    assert spec["stages"] == 1 and spec["isolated_verifier"]
    args.scripted_controls = tmp_path / "unused-controls"
    with pytest.raises(ValueError, match="cannot receive scripted"):
        make_repository_spec(args)


@pytest.mark.parametrize("tool_return_code", [0, 124])
def test_mini_scripted_scaffold_uses_declared_cwd(
    declared, tmp_path, monkeypatch, tool_return_code
):
    pytest.importorskip("psycopg")
    monkeypatch.setenv("MSWEA_GLOBAL_CONFIG_DIR", str(tmp_path / "mini-config"))
    monkeypatch.setenv("MSWEA_SILENT_STARTUP", "1")
    pytest.importorskip("minisweagent")
    import rtv.runtime.store
    from rtv.mini_agent import execute_mini

    class Store:
        def __init__(self, settings):
            pass

        @contextmanager
        def owned(self, row):
            yield

    monkeypatch.setattr(rtv.runtime.store, "Store", Store)
    calls = []

    class Env:
        async def exec(self, *, command, cwd, timeout_sec):
            import shlex

            argv = shlex.split(command)
            assert (
                argv[:6]
                == ["/usr/bin/timeout", "--signal=TERM", "--kill-after=2s", "30s", "bash", "-lc"]
                and len(argv) == 7
            )
            assert timeout_sec == 40
            command = argv[6]
            calls.append((command, cwd))
            return SimpleNamespace(
                return_code=0 if command.startswith("echo COMPLETE") else tool_return_code,
                stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"
                if command.startswith("echo COMPLETE")
                else "ok",
                stderr="",
            )

    spec = {
        **source_spec(declared),
        "adapter": "mini-fixture",
        "model": None,
        "scripted_target": ["git status"],
        "api_call_limit": 16,
    }
    result = asyncio.run(
        execute_mini(
            Env(),
            prompt(spec, False),
            tmp_path,
            settings=None,
            row={"spec": spec},
            operation="scripted",
            scripted=True,
        )
    )
    assert result.synthetic and result.exit_status == "Submitted"
    assert calls == [
        ("git status", "/testbed"),
        ("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", "/testbed"),
    ]


def test_recovery_can_use_inherited_checkpoint(tmp_path, monkeypatch):
    pytest.importorskip("psycopg")
    from rtv.runtime import control

    row = {
        "latest_checkpoint": None,
        "resume_from": "parent",
        "trial_id": "trial",
        "spec": {"stages": 2},
    }
    calls = []

    class Store:
        def __init__(self, settings):
            pass

        def recovery_result(self, trial, key):
            return None

        def checkpoint(self, key):
            calls.append(key)
            return {"stage": 1}

        def advance(self, expected, key, recovery):
            calls.append("advanced")

        def get(self, trial):
            return row

    monkeypatch.setattr(control, "Store", Store)
    monkeypatch.setattr(control.checkpoint, "validate", lambda *a: None)
    control.recover(SimpleNamespace(root=tmp_path), row, "fault")
    assert calls == ["parent", "advanced"]


def test_stage_deadline_cancels_turn_without_mislabeling_internal_timeouts():
    pytest.importorskip("psycopg")
    from rtv.runtime.supervisor import AgentStageTimeout, bounded_turn

    cancelled = []

    async def waiting():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    with pytest.raises(AgentStageTimeout, match="agent_stage_timeout"):
        asyncio.run(bounded_turn(waiting(), 0.01))
    assert cancelled == [True]

    async def internal_timeout():
        raise TimeoutError("tool failed")

    with pytest.raises(TimeoutError, match="tool failed"):
        asyncio.run(bounded_turn(internal_timeout(), 1))


def test_large_image_startup_timeout_does_not_extend_tool_timeouts(monkeypatch):
    pytest.importorskip("harbor")
    from harbor.environments.docker.docker import DockerEnvironment

    from rtv.runtime.sandbox import bounded_environment_type

    timeouts = []

    async def command(self, args, **kwargs):
        timeouts.append(kwargs["timeout_sec"])

    monkeypatch.setattr(DockerEnvironment, "_run_docker_compose_command", command)
    cls = bounded_environment_type(startup_timeout_seconds=180)
    env = cls.__new__(cls)
    asyncio.run(env._run_docker_compose_command(["up", "-d"]))
    asyncio.run(env._run_docker_compose_command(["exec", "main", "true"]))
    asyncio.run(env._run_docker_compose_command(["up"], timeout_sec=20))
    assert timeouts == [180, 60, 20]
    with pytest.raises(ValueError):
        bounded_environment_type(startup_timeout_seconds=301)


def test_agent_exec_envelope_preserves_explicit_tool_and_cleanup_timeouts(monkeypatch):
    pytest.importorskip("harbor")
    from harbor.environments.docker.docker import DockerEnvironment

    from rtv.runtime.sandbox import bounded_environment_type

    timeouts = []

    async def command(self, args, **kwargs):
        timeouts.append(kwargs["timeout_sec"])

    monkeypatch.setattr(DockerEnvironment, "_run_docker_compose_command", command)
    cls = bounded_environment_type(startup_timeout_seconds=180, exec_timeout_seconds=330)
    env = cls.__new__(cls)
    asyncio.run(env._run_docker_compose_command(["exec", "main", "codex"]))
    asyncio.run(env._run_docker_compose_command(["exec", "main", "tool"], timeout_sec=30))
    asyncio.run(env._run_docker_compose_command(["cp", "main:/workspace", "."]))
    asyncio.run(env._run_docker_compose_command(["down"]))
    asyncio.run(env._run_docker_compose_command(["up", "-d"]))
    assert timeouts == [330, 30, 60, 60, 180]
    for invalid in (0, -1, 1831, True, float("inf")):
        with pytest.raises(ValueError):
            bounded_environment_type(exec_timeout_seconds=invalid)


@pytest.mark.parametrize("reward", [0, 1])
def test_input_limit_stop_still_checkpoints_and_grades_patch(
    declared, tmp_path, monkeypatch, reward
):
    pytest.importorskip("harbor")
    pytest.importorskip("psycopg")
    from unittest.mock import AsyncMock, Mock

    from rtv.benchmark.execution import outcome
    from rtv.mini_agent import MiniResult
    from rtv.runtime import supervisor

    store = Mock()
    monkeypatch.setattr(supervisor, "Store", lambda _: store)
    monkeypatch.setattr("harbor.models.task.task.Task", lambda _: object())
    env = Mock()
    env.start = AsyncMock()
    env.session_id = "fixture"
    env.exec = AsyncMock(
        side_effect=[
            SimpleNamespace(return_code=0, stdout="tree\ntree\n"),
            SimpleNamespace(return_code=0, stdout=""),
        ]
    )

    async def download(scope, destination):
        assert scope == "/testbed"
        (destination / "fixed.py").write_text("retained patch")

    env.download_dir = download
    monkeypatch.setattr("rtv.codex_probe.make_environment", lambda *a, **k: env)
    monkeypatch.setattr("rtv.codex_probe.main_container", lambda _: "container")
    monkeypatch.setattr("rtv.harbor_probe._docker", lambda *a: declared["agent_image_id"])
    monkeypatch.setattr(supervisor.sandbox, "stop", lambda _: None)
    monkeypatch.setattr(supervisor.sandbox, "resources", lambda _: {"containers": []})
    monkeypatch.setattr(
        "rtv.runtime.handoff.load_context", lambda *a: {"text": "", "policy": "task-only"}
    )
    rollout = tmp_path / "mini-trajectory.json"
    rollout.write_text('{"exit_status": "InputLimitExceeded"}')
    monkeypatch.setattr(
        "rtv.mini_agent.execute_mini",
        AsyncMock(return_value=MiniResult(rollout, "InputLimitExceeded", 6, False)),
    )

    async def verify(task, row, workspace, output):
        assert (workspace / "fixed.py").read_text() == "retained patch"
        return {"reward": reward}, {"status": "completed", "isolated": True}

    monkeypatch.setattr("rtv.runtime.verification.isolated_verify", verify)
    monkeypatch.setattr(supervisor.checkpoint, "boundary", lambda *a: {})
    published = Mock(return_value={"checkpoint_id": "checkpoint"})
    monkeypatch.setattr(supervisor.checkpoint, "publish", published)
    row = {
        "trial_id": "trial",
        "execution_id": "execution",
        "resume_from": None,
        "spec": {**source_spec(declared), "adapter": "mini", "ttl_seconds": 900},
    }
    result = asyncio.run(supervisor.execute(None, row, tmp_path))
    assert result["agent_exit_status"] == "InputLimitExceeded"
    assert result["model_requests"] == 6
    assert result["checkpoint_id"] == "checkpoint"
    assert outcome({"status": result["status"], "result": result}) == (
        "solved" if reward else "unsolved"
    )
    assert (tmp_path / "bundle/mini-trajectory.json").exists()
    store.settle_call.assert_called_once()
    published.assert_called_once()


def test_checkpoint_validation_scratch_is_not_shared_storage(tmp_path, monkeypatch):
    pytest.importorskip("psycopg")
    from unittest.mock import Mock

    from rtv.runtime import checkpoint

    cp_id = uuid4()
    store, storage = Mock(), Mock()
    store.checkpoint_prepare.return_value = {"checkpoint_id": str(cp_id)}
    storage.root = tmp_path / "artifacts"
    storage.commit.return_value = SimpleNamespace(manifest_sha256="a" * 64)
    monkeypatch.setattr(checkpoint, "Store", lambda _: store)
    monkeypatch.setattr(checkpoint, "artifacts", lambda _: storage)
    seen = []

    def validate(settings, cp, destination):
        assert not destination.resolve().is_relative_to(tmp_path.resolve())
        destination.mkdir()
        (destination / "verified").write_text("private scratch")
        seen.append(destination)

    monkeypatch.setattr(checkpoint, "validate", validate)
    row = {**ExecutionRef(trial_id=uuid4()).to_dict(), "stage": 1}
    checkpoint.publish(
        SimpleNamespace(root=tmp_path), row, tmp_path / "bundle", {"event_cursor": 1}
    )
    assert len(seen) == 1 and not seen[0].exists()
    store.checkpoint_commit.assert_called_once()


def test_repository_hash_does_not_block_supervisor_heartbeat(tmp_path, monkeypatch):
    import threading

    from rtv.runtime import tasks

    entered, heartbeat = threading.Event(), threading.Event()

    def slow_digest(path, spec):
        entered.set()
        assert heartbeat.wait(2), "hash blocked the heartbeat event loop"
        return "verified-digest"

    monkeypatch.setattr(tasks, "workspace_digest", slow_digest)

    async def run():
        task = asyncio.create_task(tasks.async_workspace_digest(tmp_path, {}))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        heartbeat.set()
        assert await task == "verified-digest"

    asyncio.run(run())
