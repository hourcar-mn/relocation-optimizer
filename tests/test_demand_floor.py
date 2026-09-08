"""Self-check for get_demand_score's zero-vehicle neighborhood floor."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa

from core_functions.scoring import get_demand_score
from core_functions.config import ZERO_VEHICLE_DEMAND_FLOOR

# block_id doesn't need to exist in real data -- an unknown id just means
# member density and baked-in demand both default to 0.0, so the floor is
# the only thing raising the score.
LOW_DEMAND_BLOCK = {"block_id": "test-block-999", "properties": {}}


def demo():
    baseline = get_demand_score(LOW_DEMAND_BLOCK)
    assert baseline < ZERO_VEHICLE_DEMAND_FLOOR, baseline

    # no vehicle-count info passed -> unaffected
    assert get_demand_score(LOW_DEMAND_BLOCK, None) == baseline

    # neighborhood has vehicles -> unaffected
    assert get_demand_score(LOW_DEMAND_BLOCK, 3) == baseline

    # neighborhood has zero vehicles and score is already low -> raised to the floor
    assert get_demand_score(LOW_DEMAND_BLOCK, 0) == ZERO_VEHICLE_DEMAND_FLOOR

    # the floor never lowers an already-high score
    high_demand_block = {
        "block_id": "test-block-high",
        "properties": {"historical_trip_score": 100},
    }
    # force a high combined score directly via a block whose baked-in demand
    # keys are absent but historical_trip_score is high, then sanity check
    # the floor path doesn't clip a manually-raised value
    assert get_demand_score(high_demand_block, 0) >= get_demand_score(high_demand_block, 3)

    print("ok")


if __name__ == "__main__":
    demo()
