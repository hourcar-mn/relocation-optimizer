"""Self-check for build_score_weights (relo_cost independence) and
get_autonomy_score (per-model range, not one flat mileage cap)."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa
from unittest.mock import patch

from core_functions import scoring
from core_functions.scoring import build_score_weights, get_autonomy_score


def demo():
    # the four positive weights always sum to 1.0, no matter what relo_cost is --
    # raising relo_cost must not shrink the others
    default = build_score_weights()
    positive_keys = ["demand", "charger", "battery", "annual_demand"]
    assert round(sum(default[k] for k in positive_keys), 9) == 1.0, default

    high_relo = build_score_weights(relo_cost=0.9)
    for k in positive_keys:
        assert high_relo[k] == default[k], (k, high_relo, default)
    assert high_relo["relo_cost"] == 0.9

    # a full-charge vehicle should score 100 regardless of its model's max
    # range, i.e. a full Leaf (150mi) and a full Equinox (319mi) score the same
    with patch.object(scoring, "get_current_temp_f", return_value=70):
        leaf_full = {"name": "Nissan Leaf 101", "autonomy": 150 / 0.621371}
        equinox_full = {"name": "Chevy Equinox 207", "autonomy": 319 / 0.621371}
        assert get_autonomy_score(leaf_full) == 100.0, get_autonomy_score(leaf_full)
        assert get_autonomy_score(equinox_full) == 100.0, get_autonomy_score(equinox_full)

        # half-charged leaf should score ~50
        leaf_half = {"name": "Nissan Leaf 101", "autonomy": 75 / 0.621371}
        assert abs(get_autonomy_score(leaf_half) - 50.0) < 1.0, get_autonomy_score(leaf_half)

    print("ok")


if __name__ == "__main__":
    demo()
