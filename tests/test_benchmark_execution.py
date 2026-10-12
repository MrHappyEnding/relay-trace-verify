"""Mocked runtime evidence contracts, not model or cluster success measurements."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("psycopg")

from rtv.benchmark.execution import collect, outcome, prepare_pair, validate_run
from rtv.benchmark.protocol import digest, load, matrix
from rtv.harbor_probe import tree_digest


def pair(tmp_path):
    protocol = load()
    task = next(t for t in protocol["tasks"] if t["split"] == "pilot")
    source = tmp_path / "task"
    source.mkdir()
    (source / "instruction.md").write_text("mock public issue")
    body = dict(
        schema_version=1,
        kind="swebench",
        instance_id=task["instance_id"],
        split="pilot",
        workspace="/testbed",
        workspace_excludes=[".DS_Store"],
        platform="linux/amd64",
        task_directory=str(source),
        task_digest=tree_digest(source),
        protocol_sha256=protocol["protocol_sha256"],
        base_commit=task["base_commit"],
        agent_image_id="sha256:" + "a" * 64,
        verifier_image_id="sha256:" + "b" * 64,
        verifier_timeout_seconds=600,
    )
    contract = {**body, "contract_sha256": digest(body)}
    spec = dict(
        task_contract=contract,
        task_digest=contract["task_digest"],
        image_id=contract["agent_image_id"],
        task_name=task["instance_id"],
        adapter="fixture",
        model=None,
        max_recoveries=0,
        ttl_seconds=1800,
        scripted_source=["true"],
        scripted_target=["true"],
    )
    return protocol, prepare_pair(protocol, spec, tmp_path / "runtime")


class MockStore:
    def __init__(self, run):
        self.settings = SimpleNamespace(root=Path(run["root"]))
        self.rows, self.ledgers = {}, {}
        for expected in [run["source"], *run["entries"]]:
            prefix = "branch" not in expected
            fresh = expected.get("branch") == "B-fresh"
            tid = expected["trial_id"]
            self.rows[tid] = dict(
                trial_id=tid,
                execution_id=tid,
                created_at=datetime(2026, 10, 12, tzinfo=UTC),
                cleanup="complete",
                spec=expected["spec"],
                status="sealed" if prefix else "succeeded",
                latest_checkpoint="source-cp" if prefix else "own-cp",
                parent_checkpoint=None if prefix or fresh else "source-cp",
                result={
                    "verification": {"status": "completed", "isolated": True},
                    "rewards": {"reward": 1.0},
                },
            )
            self.ledgers[tid] = dict(
                cost_usd=0,
                known_api_cost_usd=0,
                unknown_operations=0,
                operations=[{"kind": "fixture", "operation_id": "prefix-op" if prefix else tid}],
                prefix_operation_ids=[] if prefix or fresh else ["prefix-op"],
            )

    def get(self, tid):
        if tid not in self.rows:
            raise ValueError("Unknown trial in this runtime root")
        return self.rows[tid]

    def ledger(self, tid):
        return self.ledgers[tid]

    def events(self, tid):
        branch = self.rows[tid]["spec"]["benchmark_assignment"].get("branch")
        policies = {"A-continue": "native", "B-workspace": "task-only", "B-summary": "summary"}
        return (
            [{"kind": "handoff.completed", "payload": {"policy": policies[branch]}}]
            if branch in policies
            else []
        )

    def checkpoint(self, cid):
        assert cid == "source-cp"
        return {"context": {"result": {"status": "sealed"}}}

    def executions(self, tid):
        return []


def test_assignment_freezes_order_budgets_and_trial_identity(tmp_path):
    protocol, run = pair(tmp_path)
    validate_run(protocol, run)
    expected = [
        r for r in matrix(protocol, "pilot") if r["pair_id"] == run["entries"][0]["pair_id"]
    ]
    assert [e["branch"] for e in run["entries"]] == [e["branch"] for e in expected]
    assert len({e["trial_id"] for e in run["entries"]}) == 4
    fresh = next(e for e in run["entries"] if e["branch"] == "B-fresh")
    assert fresh["spec"]["agent_seconds"] == 600 and fresh["spec"]["api_call_limit"] == 16
    changed = deepcopy(run)
    changed["entries"].reverse()
    changed["run_sha256"] = digest({k: v for k, v in changed.items() if k != "run_sha256"})
    with pytest.raises(ValueError, match="assignment changed"):
        validate_run(protocol, changed)


def test_runtime_collection_preserves_missing_and_unknown(tmp_path):
    protocol, run = pair(tmp_path)
    store = MockStore(run)
    uncertain = run["entries"][0]["trial_id"]
    store.ledgers[uncertain].update(cost_usd=None, unknown_operations=1, known_api_cost_usd=0.02)
    report = collect(protocol, run, store, tmp_path / "out")
    assert report["received"] == 4 and report["missing"] == 12
    row = next(r for r in report["observations"] if r.get("trial_id") == uncertain)
    assert row["incremental_api_cost_usd"] is None and row["known_incremental_api_cost_usd"] == 0.02
    assert all(c["ci95"] is None for c in report["comparisons"].values())
    provenance = json.loads((tmp_path / "out/provenance.json").read_text())
    assert len(provenance["runtime_sha256"]) == 6


@pytest.mark.parametrize("damage", ["spec", "prefix", "real-operation", "fresh-prefix"])
def test_collection_rejects_wrong_evidence_lineage(tmp_path, damage):
    protocol, run = pair(tmp_path)
    store = MockStore(run)
    entry = next(
        e
        for e in run["entries"]
        if e["branch"] == ("B-fresh" if damage == "fresh-prefix" else "B-summary")
    )
    row = store.rows[entry["trial_id"]]
    if damage == "spec":
        row["spec"] = {**row["spec"], "agent_seconds": 1}
    elif damage in {"prefix", "fresh-prefix"}:
        row["parent_checkpoint"] = "wrong"
    else:
        store.ledgers[entry["trial_id"]]["operations"][0]["kind"] = "mini-api"
    with pytest.raises(ValueError):
        collect(protocol, run, store, tmp_path / "out")


def test_source_failure_keeps_fresh_and_does_not_invent_continuations(tmp_path):
    protocol, run = pair(tmp_path)
    store = MockStore(run)
    store.rows[run["source"]["trial_id"]].update(status="failed", latest_checkpoint=None)
    for entry in run["entries"]:
        if entry["branch"] != "B-fresh":
            del store.rows[entry["trial_id"]]
    report = collect(protocol, run, store, tmp_path / "out")
    assert report["received"] == 4
    assert sum(r["status"] == "source_failed" for r in report["observations"]) == 3
    assert report["arms"]["B-fresh"]["outcomes"]["solved"] == 1


def test_verifier_failure_is_not_a_graded_zero():
    row = dict(status="failed", result={"rewards": {"reward": 0}})
    assert outcome(row) == "infra_error"
    row["result"]["verification"] = {"status": "completed", "isolated": True}
    assert outcome(row) == "unsolved"
    row["status"] = "cancelled"
    assert outcome(row) == "cancelled"


def test_collector_accepts_retained_prefix_after_source_cleanup(tmp_path):
    protocol, run = pair(tmp_path)
    store = MockStore(run)
    store.rows[run["source"]["trial_id"]].update(status="cancelled", result={"reason": "cancelled"})
    report = collect(protocol, run, store, tmp_path / "out")
    assert report["received"] == 4
    assert all(r["status"] in {"solved", "missing"} for r in report["observations"])


def test_collector_rejects_policy_label_without_matching_transfer(tmp_path):
    protocol, run = pair(tmp_path)
    store = MockStore(run)
    store.events = lambda tid: []
    with pytest.raises(ValueError, match="context policy"):
        collect(protocol, run, store, tmp_path / "out")
