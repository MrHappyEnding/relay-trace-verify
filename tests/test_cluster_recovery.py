"""Explicit recovery grants do not widen ordinary benchmark credentials."""

import runpy
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from test_cluster_credentials import real_pair, row

from rtv.cluster.credentials import admitted_reference, authorize_recovery, validate_access
from rtv.cluster.recovery import prepare_run, validate_run


def recovery(tmp_path):
    _, paired = real_pair(tmp_path)
    source = dict(paired["source"]["spec"])
    source.pop("benchmark_assignment")
    run = prepare_run(source, tmp_path / "recovery")
    refs = {"codex": {"name": "rtv-model-test-codex", "uid": "uid-codex"}}
    access = authorize_recovery(run, refs)
    return run, access


def test_recovery_grant_only_admits_the_inherited_checkpoint_once(tmp_path):
    run, access = recovery(tmp_path)
    validate_run(run)
    pool = {"model_access": access}
    source = row(run["source"])
    assert admitted_reference(pool, run["root"], source) == access["secrets"]["codex"]
    target = row(run["entries"][0])
    target.update(parent_checkpoint="committed-source", resume_from="committed-source")
    assert admitted_reference(pool, run["root"], target)
    target.update(epoch=2, recoveries=1)
    assert admitted_reference(pool, run["root"], target)
    for changes in (
        {"epoch": 3, "recoveries": 2},
        {"epoch": 2, "recoveries": 0},
        {"resume_from": "another-checkpoint"},
        {"parent_checkpoint": None},
        {"recoveries": True},
    ):
        with pytest.raises(ValueError):
            admitted_reference(pool, run["root"], {**target, **changes})
    with pytest.raises(ValueError):
        admitted_reference(pool, run["root"], {**source, "epoch": 2, "recoveries": 1})


@pytest.mark.parametrize("damage", ["api", "allowance", "purpose", "spec", "root", "plan"])
def test_recovery_admission_rejects_mutated_authority(tmp_path, damage):
    run, access = recovery(tmp_path)
    current = row(run["entries"][0])
    if damage == "api":
        access["grants"][current["trial_id"]]["requests"] = 1
    elif damage == "allowance":
        access["grants"][current["trial_id"]]["max_recoveries"] = 2
    elif damage == "purpose":
        access.pop("purpose")
    elif damage == "spec":
        current["spec"] = {**current["spec"], "max_recoveries": 2}
    elif damage == "root":
        access["root"] += "-other"
    else:
        run["maximum_codex_stages"] = 4
        with pytest.raises(ValueError):
            authorize_recovery(run, access["secrets"])
        return
    with pytest.raises(ValueError):
        admitted_reference({"model_access": access}, run["root"], current)


def test_zero_api_budget_and_no_mini_secret_in_recovery_profile(tmp_path):
    _, access = recovery(tmp_path)
    assert access["approved_budget_usd"] == 0
    for changes in (
        {"approved_budget_usd": 1},
        {"secrets": {**access["secrets"], "mini": access["secrets"]["codex"]}},
    ):
        with pytest.raises(ValueError):
            validate_access({**deepcopy(access), **changes})


def test_recovery_inspector_does_not_refresh_git_index(tmp_path):
    from rtv.harbor_probe import tree_digest

    inspect = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/m4_model_recovery.py")
    )["changed_paths"]
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)

    git("init", "-q")
    file = repo / "code.py"
    file.write_text("before\n")
    git("add", "code.py")
    git(
        "-c",
        "user.name=RTV Test",
        "-c",
        "user.email=rtv@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "base",
    )
    # Force stale cached stat information, as occurs after checkpoint transport.
    file.write_text("after\n")
    before = tree_digest(repo)
    assert inspect(repo, "HEAD") == ["code.py"]
    assert tree_digest(repo) == before


def test_runner_failure_diagnostics_omit_exception_text_and_credentials(tmp_path, monkeypatch):
    import json
    from uuid import uuid4

    from rtv.cluster.runner import record_startup_failure

    monkeypatch.setenv("RTV_RUNTIME_ROOT", str(tmp_path))
    trial, execution = str(uuid4()), str(uuid4())
    try:
        raise ValueError("private-provider-token-do-not-persist")
    except ValueError as error:
        record_startup_failure(trial, execution, error)
    raw = (tmp_path / trial / "executions" / execution / "runner-error.json").read_text()
    assert "private-provider-token" not in raw
    assert json.loads(raw)["error_type"] == "ValueError"
    assert (
        json.loads(raw)["frames"][-1]["function"]
        == "test_runner_failure_diagnostics_omit_exception_text_and_credentials"
    )
