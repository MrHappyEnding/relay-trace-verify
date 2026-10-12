"""Validate offline verifier controls without confusing harness errors with failure."""

import json


def control_result(directory, instance_id, expected, exit_code):
    # The pinned Harbor parser returns 1 for a valid unresolved test result.
    if expected not in (0, 1) or exit_code != 1 - expected:
        raise ValueError("Verifier exit code disagrees with the expected control")
    result = json.loads((directory / "report.json").read_text())[instance_id]
    reward = int((directory / "reward.txt").read_text())
    if result.get("patch_successfully_applied") is not True:
        raise ValueError("Official parser did not find valid test output")
    if reward != expected or result.get("resolved") is not bool(expected):
        raise ValueError("Reference control disagrees with expected outcome")
    return {"reward": reward, "expected": expected, "passed": True, "verifier_exit_code": exit_code}
