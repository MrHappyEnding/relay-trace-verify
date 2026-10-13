"""Fail-closed cumulative admission for serial immutable benchmark pairs."""

from decimal import Decimal


def exposure(state):
    total = Decimal(str(state["prior_cost_usd"]))
    for pair in state["pairs"].values():
        if pair["state"] == "started":
            total += Decimal(str(pair["reservation_usd"]))
        elif pair["state"] == "settled":
            total += Decimal(str(pair["known_cost_usd"])) + Decimal(
                str(pair["unknown_reservation_usd"])
            )
    return total


def admit(state, key, reservation):
    amount = Decimal(str(reservation))
    if key in state["pairs"]:
        raise ValueError("Pair already admitted; paid replay is forbidden")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Invalid reservation")
    if any(p["state"] == "started" for p in state["pairs"].values()):
        raise ValueError("An interrupted pair must be reconciled before further spending")
    if exposure(state) + amount > Decimal(str(state["approved_total_usd"])):
        raise ValueError("Cumulative approved budget cannot cover the next pair")
    state["pairs"][key] = {"state": "started", "reservation_usd": float(amount)}


def settle(state, key, report, per_request):
    pair = state["pairs"][key]
    if pair["state"] != "started":
        raise ValueError("Only an outstanding reservation can settle")
    known = Decimal(str(report["known_api_cost_usd"]))
    unknown = report["unknown_api_requests"]
    requests = report["api_requests"]
    if (
        not known.is_finite()
        or known < 0
        or type(unknown) is not int
        or type(requests) is not int
        or not 0 <= unknown <= requests <= 32
    ):
        raise ValueError("Invalid API accounting; keep reservation outstanding")
    hold = Decimal(str(per_request)) * unknown
    if known + hold > Decimal(str(pair["reservation_usd"])):
        raise ValueError("Settlement exceeds admission; stop before any further spending")
    pair.update(state="settled", known_cost_usd=float(known), unknown_reservation_usd=float(hold))
