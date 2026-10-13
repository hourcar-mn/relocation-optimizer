"""Self-checks for the MILP constraints and the JSON sanitizer.

solve_milp is the core of the product: if a constraint stops binding, the run
still succeeds and the output still looks plausible, so nothing else catches
it. sanitize_deep guards the API boundary — a NaN that survives it becomes
invalid JSON in the dashboard. Run: python tests/test_optimizer.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa

import math
import numpy as np
from core_functions.milp_solver import solve_milp
from core_functions.assignment_builder import sanitize_deep

# 40 miles is MIN_AUTONOMY_LEVEL; autonomy is kilometers, so 30 km (~19 mi) needs
# a plug and 400 km (~249 mi) does not.
FULL = {"name": "full", "autonomy": 400}
LOW = {"name": "low", "autonomy": 30}

BLOCK_A = {"block_id": "A", "lat": 44.98, "lon": -93.27}
BLOCK_B = {"block_id": "B", "lat": 44.95, "lon": -93.10}
BLOCK_C = {"block_id": "C", "lat": 44.93, "lon": -93.20}


def test_vehicle_assigned_at_most_once():
    blocks = [BLOCK_A, BLOCK_B]
    scores = {("full", "A"): 90.0, ("full", "B"): 80.0}
    assignments = solve_milp(
        scores,
        [FULL],
        blocks,
        {"A": "North", "B": "South"},
        {"North": 4, "South": 4},
        [],
    )
    assert len(assignments) == 1, assignments
    # and it takes the better of the two
    assert assignments[0][1] == "A", assignments


def test_neighborhood_capacity_binds():
    blocks = [BLOCK_A, BLOCK_B, BLOCK_C]
    evies = [{"name": f"v{i}", "autonomy": 400} for i in range(3)]
    scores = {(v["name"], b["block_id"]): 90.0 for v in evies for b in blocks}
    # all three blocks sit in one neighborhood that can only absorb one car
    assignments = solve_milp(
        scores, evies, blocks, {"A": "North", "B": "North", "C": "North"}, {"North": 1}, []
    )
    assert len(assignments) == 1, assignments


def test_charging_vehicle_needs_an_open_plug():
    blocks = [BLOCK_A]
    scores = {("low", "A"): 90.0}
    no_plugs = solve_milp(scores, [LOW], blocks, {"A": "North"}, {"North": 4}, [])
    assert no_plugs == [], no_plugs

    # same setup, but now a charger with one free plug sits on that block
    charger = {"lat": 44.98, "lon": -93.27, "available": 1}
    with_plug = solve_milp(
        scores, [LOW], blocks, {"A": "North"}, {"North": 4}, [charger]
    )
    assert len(with_plug) == 1, with_plug


def test_full_battery_vehicle_ignores_plug_capacity():
    # the plug constraint only covers vehicles that actually need charging
    blocks = [BLOCK_A]
    scores = {("full", "A"): 90.0}
    assignments = solve_milp(scores, [FULL], blocks, {"A": "North"}, {"North": 4}, [])
    assert len(assignments) == 1, assignments


def test_sanitize_deep_replaces_non_finite():
    dirty = {
        "a": float("nan"),
        "b": float("inf"),
        "c": [1.5, float("-inf"), {"d": np.float64("nan")}],
        "e": np.int64(7),
        "f": np.bool_(True),
        "g": np.float64(2.5),
        "h": "text",
    }
    clean = sanitize_deep(dirty)
    assert clean["a"] is None
    assert clean["b"] is None
    assert clean["c"] == [1.5, None, {"d": None}]
    assert clean["e"] == 7 and type(clean["e"]) is int
    assert clean["f"] is True and type(clean["f"]) is bool
    assert clean["g"] == 2.5 and type(clean["g"]) is float
    assert clean["h"] == "text"


def test_sanitize_deep_leaves_finite_values_alone():
    payload = {"score": 62.5, "tags": ["a", "b"], "ok": True, "n": None}
    assert sanitize_deep(payload) == payload


if __name__ == "__main__":
    test_vehicle_assigned_at_most_once()
    test_neighborhood_capacity_binds()
    test_charging_vehicle_needs_an_open_plug()
    test_full_battery_vehicle_ignores_plug_capacity()
    test_sanitize_deep_replaces_non_finite()
    test_sanitize_deep_leaves_finite_values_alone()
    print("ok")
