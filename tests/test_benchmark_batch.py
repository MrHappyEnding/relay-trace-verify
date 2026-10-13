from copy import deepcopy
from decimal import Decimal

import pytest

from rtv.benchmark.batch import admit, exposure, settle


def test_serial_admission_releases_unused_allowance_but_retains_unknown():
    state = {"prior_cost_usd": 0.85497195, "approved_total_usd": 30, "pairs": {}}
    admit(state, "one", 5.12)
    assert exposure(state) == Decimal("5.97497195")
    with pytest.raises(ValueError, match="interrupted"):
        admit(state, "two", 5.12)
    settle(
        state, "one", dict(known_api_cost_usd=0.05, unknown_api_requests=2, api_requests=10), 0.16
    )
    assert exposure(state) == Decimal("1.22497195")
    with pytest.raises(ValueError, match="replay"):
        admit(state, "one", 5.12)
    admit(state, "two", 5.12)
    state["approved_total_usd"] = 6
    settle(
        state, "two", dict(known_api_cost_usd=0.01, unknown_api_requests=0, api_requests=1), 0.16
    )
    with pytest.raises(ValueError, match="budget"):
        admit(state, "three", 5.12)


@pytest.mark.parametrize("cost,count", [(6, 0), (float("nan"), 0), (-1, 0), (0.1, 33)])
def test_bad_settlement_preserves_full_hold(cost, count):
    state = {"prior_cost_usd": 0, "approved_total_usd": 30, "pairs": {}}
    admit(state, "one", 5.12)
    original = deepcopy(state)
    with pytest.raises(ValueError):
        settle(
            state,
            "one",
            dict(known_api_cost_usd=cost, unknown_api_requests=count, api_requests=32),
            0.16,
        )
    assert state == original
