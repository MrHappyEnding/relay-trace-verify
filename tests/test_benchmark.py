import hashlib
import json
from copy import deepcopy

import pytest

from rtv.benchmark.analysis import analyze, cluster_difference
from rtv.benchmark.protocol import DEFAULT_PLAN, load, matrix, reservation_plan, seal, selection
from rtv.benchmark.source import audit, catalog_for
from rtv.benchmark.verification import control_result
from rtv.cli import main


def observations(plan, split="pilot"):
    return [
        {
            **spec,
            "mode": "scripted",
            "status": "solved" if spec["branch"] == "B-summary" else "unsolved",
            "checkpoint_id": None if spec["branch"] == "B-fresh" else spec["pair_id"],
            "prefix_api_cost_usd": 0 if spec["branch"] == "B-fresh" else 0.1,
            "incremental_api_cost_usd": 0.2,
            "unknown_operations": 0,
            "verifier_status": "completed",
            "verifier_reward": int(spec["branch"] == "B-summary"),
        }
        for spec in matrix(plan, split)
    ]


def reseal(plan):
    return seal({k: v for k, v in plan.items() if k != "protocol_sha256"})


def test_frozen_selection_is_order_invariant_and_disjoint():
    plan = load()
    catalog = catalog_for(DEFAULT_PLAN)
    assert audit(plan, catalog)["selected_tasks"] == 24
    original = selection(catalog["records"])
    assert original == selection(list(reversed(catalog["records"])))
    pilot = {r["instance_id"] for r in original["pilot"]}
    evaluation = {r["instance_id"] for r in original["evaluation"]}
    assert len(pilot) == 4 and len(evaluation) == 20 and not pilot & evaluation
    changed = deepcopy(plan)
    changed["tasks"][0]["split"] = "evaluation"
    with pytest.raises(ValueError, match="identity or contents"):
        matrix(changed, "pilot")
    with pytest.raises(ValueError, match="Duplicate task"):
        selection(catalog["records"] + [catalog["records"][0]])


def test_source_audit_detects_tampering_and_extra_files(tmp_path):
    plan = load()
    catalog = catalog_for(DEFAULT_PLAN)
    for task in plan["tasks"]:
        directory = tmp_path / task["harbor_path"]
        directory.mkdir(parents=True)
        (directory / "fixture.txt").write_bytes(b"source")
        task["files_sha256"] = {"fixture.txt": hashlib.sha256(b"source").hexdigest()}
    plan = reseal(plan)
    assert audit(plan, catalog, tmp_path)["verified_files"] == 24
    first = tmp_path / plan["tasks"][0]["harbor_path"]
    (first / "fixture.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Upstream files changed"):
        audit(plan, catalog, tmp_path)
    (first / "fixture.txt").write_bytes(b"source")
    (first / "extra.txt").write_text("unfrozen")
    with pytest.raises(ValueError, match="Upstream files changed"):
        audit(plan, catalog, tmp_path)
    (first / "extra.txt").unlink()
    (first / "link").symlink_to("fixture.txt")
    with pytest.raises(ValueError, match="symlink"):
        audit(plan, catalog, tmp_path)


def test_matrix_budget_and_stable_pair_identity():
    plan = load()
    rows = matrix(plan, "evaluation")
    assert rows == matrix(plan, "evaluation")
    assert len({r["observation_id"] for r in rows}) == 160
    assert len({r["pair_id"] for r in rows}) == 40
    assert len(matrix(plan, "pilot")) == 16
    assert reservation_plan(plan, "pilot")["api_reservation_usd"] == 7.936
    budget = reservation_plan(plan, "evaluation")
    assert budget["max_api_requests"] == 1280
    assert budget["api_reservation_usd"] == 79.36
    assert budget["paid_execution_enabled"] is False


def test_missing_rows_and_infrastructure_failure_remain_in_denominator():
    plan = load()
    rows = observations(plan)
    summaries = [r for r in rows if r["branch"] == "B-summary"]
    summaries[0].update(status="infra_error", verifier_status="failed", verifier_reward=None)
    rows.remove(summaries[1])
    report = analyze(plan, rows, "pilot", mode="scripted", bootstrap_samples=100)
    arm = report["arms"]["B-summary"]
    assert arm["resolved_itt"] == 0.5 and arm["resolved_among_graded"] == 1
    assert arm["planned"] == 4 and arm["graded"] == 2
    assert arm["outcomes"] == {"infra_error": 1, "missing": 1, "solved": 2}
    primary = report["comparisons"]["B-summary minus A-continue"]
    assert primary["ci95"] is None and primary["provisional"]
    assert primary["planned_pairs"] == 4 and primary["complete_graded_pairs"] == 2


def test_unknown_cost_is_not_zero_filled_and_prefix_is_included_per_strategy():
    plan = load()
    rows = observations(plan)
    row = next(r for r in rows if r["branch"] == "B-workspace")
    row.update(incremental_api_cost_usd=None, unknown_operations=1)
    report = analyze(plan, rows, "pilot", mode="scripted", bootstrap_samples=100)
    arm = report["arms"]["B-workspace"]
    assert arm["known_incremental_api_cost_usd"] == pytest.approx(0.6)
    assert arm["known_end_to_end_api_cost_usd"] == pytest.approx(1.0)
    assert arm["unknown_cost_rows"] == arm["unknown_end_to_end_cost_rows"] == 1
    assert arm["unknown_operations"] == 1
    assert report["arms"]["B-fresh"]["known_end_to_end_api_cost_usd"] == pytest.approx(0.8)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"mode": "real-model"}, "Mixed evidence"),
        ({"checkpoint_id": "different"}, "Paired branches disagree"),
        ({"pair_id": "forged"}, "override planned"),
        ({"incremental_api_cost_usd": float("nan")}, "finite"),
        ({"unknown_operations": 1}, "uncertain operation"),
        ({"verifier_status": "crashed"}, "completed verifier"),
        ({"verifier_reward": 0}, "binary verifier reward"),
        ({"status": "source_failed"}, "Source failure"),
    ],
)
def test_rejects_incoherent_observations(mutation, error):
    plan = load()
    rows = observations(plan)
    next(r for r in rows if r["branch"] == "B-summary").update(mutation)
    with pytest.raises(ValueError, match=error):
        analyze(plan, rows, "pilot", mode="scripted", bootstrap_samples=100)


def test_rejects_duplicate_runs_fresh_checkpoints_and_partial_source_failure():
    plan = load()
    rows = observations(plan)
    with pytest.raises(ValueError, match="duplicate observation"):
        analyze(plan, rows + [rows[0]], "pilot", mode="scripted")
    fresh = next(r for r in rows if r["branch"] == "B-fresh")
    fresh["checkpoint_id"] = "unexpected"
    with pytest.raises(ValueError, match="Fresh baseline"):
        analyze(plan, rows, "pilot", mode="scripted")
    fresh["checkpoint_id"] = None
    next(r for r in rows if r["branch"] == "B-summary").update(
        status="source_failed", checkpoint_id=None
    )
    with pytest.raises(ValueError, match="source failure"):
        analyze(plan, rows, "pilot", mode="scripted")


def test_prefix_accounting_remains_consistent_without_a_checkpoint():
    plan = load()
    rows = observations(plan)
    pair = rows[0]["pair_id"]
    branches = [r for r in rows if r["pair_id"] == pair and r["branch"] != "B-fresh"]
    for row in branches:
        row.update(status="source_failed", checkpoint_id=None)
    branches[0]["prefix_api_cost_usd"] = None
    with pytest.raises(ValueError, match="prefix accounting"):
        analyze(plan, rows, "pilot", mode="scripted")
    del rows[0]["incremental_api_cost_usd"]
    with pytest.raises(ValueError, match="Explicit cost fields"):
        analyze(plan, rows, "pilot", mode="scripted")


def test_repeats_cannot_reuse_one_prefix_checkpoint():
    plan = load()
    rows = observations(plan, "evaluation")
    task = rows[0]["instance_id"]
    for row in rows:
        if row["instance_id"] == task and row["branch"] != "B-fresh":
            row["checkpoint_id"] = "one-source-for-two-repeats"
    with pytest.raises(ValueError, match="Different task repeats"):
        analyze(plan, rows, "evaluation", mode="scripted")


def test_bootstrap_keeps_repeats_within_task_and_withholds_single_task_ci():
    plan = load()
    rows = observations(plan, "evaluation")
    task_ids = [t["instance_id"] for t in plan["tasks"] if t["split"] == "evaluation"]
    for row in rows:
        task_index = task_ids.index(row["instance_id"])
        solved = (task_index < 10) == (row["branch"] == "B-summary")
        row.update(status="solved" if solved else "unsolved", verifier_reward=int(solved))
    report = analyze(plan, rows, "evaluation", mode="scripted")
    primary = report["comparisons"]["B-summary minus A-continue"]
    expected = cluster_difference([1] * 10 + [-1] * 10)
    assert primary["tasks"] == 20 and primary["planned_pairs"] == 40
    assert primary["difference"] == expected["difference"] == 0
    assert primary["ci95"] == expected["ci95"]
    assert cluster_difference([1])["ci95"] is None
    empty = analyze(plan, [], "evaluation", mode="scripted", bootstrap_samples=100)
    assert empty["missing"] == 160
    assert empty["comparisons"]["B-summary minus A-continue"]["difference"] is None


def test_cli_report_preserves_input_hash_modes_and_refuses_overwrite(tmp_path, capsys):
    records = tmp_path / "records.jsonl"
    records.write_text("".join(json.dumps(r) + "\n" for r in observations(load())))
    output = tmp_path / "report"
    args = [
        "benchmark",
        "analyze",
        "--records",
        str(records),
        "--output",
        str(output),
        "--split",
        "pilot",
        "--mode",
        "scripted",
    ]
    assert main(args) == 0
    capsys.readouterr()
    report = json.loads((output / "report.json").read_text())
    assert report["raw_records_sha256"] == hashlib.sha256(records.read_bytes()).hexdigest()
    assert (output / "records.jsonl").read_bytes() == records.read_bytes()
    assert "No model-effect claim" in report["inference"]
    assert (output / "report.en.md").exists() and (output / "summary.csv").exists()
    assert main(args) == 1
    assert "rtv:" in capsys.readouterr().err


def test_verifier_parser_error_cannot_count_as_successful_negative_control(tmp_path):
    (tmp_path / "reward.txt").write_text("0")
    with pytest.raises(FileNotFoundError):
        control_result(tmp_path, "task", 0, 1)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"task": {"resolved": False}}))
    with pytest.raises(ValueError, match="valid test output"):
        control_result(tmp_path, "task", 0, 1)
    report_path.write_text(
        json.dumps({"task": {"resolved": False, "patch_successfully_applied": True}})
    )
    assert control_result(tmp_path, "task", 0, 1)["passed"]
    with pytest.raises(ValueError, match="exit code"):
        control_result(tmp_path, "task", 0, 2)
    with pytest.raises(ValueError, match="expected outcome"):
        control_result(tmp_path, "task", 1, 0)
