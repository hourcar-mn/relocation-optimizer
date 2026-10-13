"""Self-checks for the score weights and the capacity tiers.

Weights arrive from user-supplied API query params, so a normalize that drifts
silently skews every score in the run without erroring.
Run: python tests/test_scoring.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa

from core_functions.scoring import build_score_weights
from core_functions.neighborhoods import get_neighborhood_capacity
from core_functions.config import haversine

# relo_cost is a separate penalty, so only these four are renormalized to 1
POSITIVE = {"demand", "charger", "battery", "annual_demand"}


def positive_sum(w):
    return sum(w[k] for k in POSITIVE)


def test_defaults_sum_to_one():
    w = build_score_weights()
    assert set(w) == POSITIVE | {"relo_cost"}, w
    assert abs(positive_sum(w) - 1.0) < 1e-9, w


def test_overrides_are_renormalized():
    w = build_score_weights(demand=2, charger=2, battery=2, annual_demand=2, relo_cost=2)
    for k in POSITIVE:
        assert abs(w[k] - 0.25) < 1e-9, w
    assert w["relo_cost"] == 2, w


def test_single_nonzero_weight_takes_everything():
    w = build_score_weights(demand=1, charger=0, battery=0, annual_demand=0, relo_cost=0)
    assert abs(w["demand"] - 1.0) < 1e-9, w
    assert abs(positive_sum(w) - 1.0) < 1e-9, w


def test_all_zero_falls_back_to_defaults():
    w = build_score_weights(demand=0, charger=0, battery=0, annual_demand=0)
    assert w == build_score_weights(), w


def test_negative_relo_cost_is_clamped():
    assert build_score_weights(relo_cost=-1)["relo_cost"] == 0.0


def test_capacity_tier_boundaries():
    # the tiers are inclusive at 70/40/15 — off-by-one here changes how many
    # cars a neighborhood can absorb
    assert get_neighborhood_capacity(100) == 4
    assert get_neighborhood_capacity(70) == 4
    assert get_neighborhood_capacity(69.9) == 3
    assert get_neighborhood_capacity(40) == 3
    assert get_neighborhood_capacity(39.9) == 2
    assert get_neighborhood_capacity(15) == 2
    assert get_neighborhood_capacity(14.9) == 1
    assert get_neighborhood_capacity(0) == 1


def test_haversine_returns_miles():
    # one degree of latitude is ~69 miles anywhere on the globe
    assert abs(haversine(44.0, -93.0, 45.0, -93.0) - 69.1) < 0.2
    # Minneapolis City Hall to the Saint Paul Capitol, ~8.1 miles apart
    miles = haversine(44.9773, -93.2655, 44.9553, -93.1022)
    assert 7.8 < miles < 8.5, miles
    assert haversine(44.98, -93.27, 44.98, -93.27) == 0.0


if __name__ == "__main__":
    test_defaults_sum_to_one()
    test_overrides_are_renormalized()
    test_single_nonzero_weight_takes_everything()
    test_all_zero_falls_back_to_defaults()
    test_negative_relo_cost_is_clamped()
    test_capacity_tier_boundaries()
    test_haversine_returns_miles()
    print("ok")
