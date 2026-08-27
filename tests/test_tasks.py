"""Self-check for the assignment -> demand-dashboard task mapping."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa

from core_functions.tasks import assignments_to_tasks

SAMPLE = {
    "vehicle": "207 Chevy Equinox",
    "vehicle_id": "00e34436-31cf-46e8-b4b1-9e744861a9fd",
    "plate": "207 - 123456",
    "block": "270530001011",
    "score": 62.5,
    "score_breakdown": {"demand": 71.0},
    "neighborhood": "Downtown West",
    "origin_neighborhood": "Como",
    "demand_label": "High demand",
    "charger_label": "Charging not needed and no open charger nearby",
    "is_required": True,
    "is_recommended": False,
    "is_urgent": True,
    "autonomy_miles": 274.0,
    "autonomy2_pct": 86.0,
    "hours_sitting": 51.2,
    "from_block": "270530012345",
    "current_lat": 44.967942,
    "current_lon": -93.050522,
    "drop_off_lat": 44.975,
    "drop_off_lon": -93.271,
    "drive_time": 18.0,
    "drive_distance": 9.4,
    "relocation_cost_usd": 31.2,
    "net_value_usd": -12.3,
    "worth_it": False,
}


def demo():
    low = {**SAMPLE, "vehicle_id": "other", "score": 10.0}
    tasks = assignments_to_tasks([low, SAMPLE], "2026-08-17T12:00:00+00:00")

    # highest score first, priority rescaled into the dashboard's 0-1 range
    assert [t["priority"] for t in tasks] == [0.625, 0.1], tasks
    t = tasks[0]
    assert t["type"] == "relocate" and t["status"] == "pending"
    # id is stable across runs, so a re-polling dashboard keeps its selection
    assert t["id"] == "relocate-00e34436-31cf-46e8-b4b1-9e744861a9fd"
    assert assignments_to_tasks([SAMPLE], "a later run")[0]["id"] == t["id"]
    assert t["vehicle"]["battery"]["percentage"] == 86.0
    assert t["vehicle"]["location"] == {"latitude": 44.967942, "longitude": -93.050522}
    assert t["destination"]["name"] == "Downtown West"
    assert t["details"]["fromNeighborhood"] == "Como"
    assert t["distance"] == 9.4
    assert t["estimatedTime"] == 23  # 18 min drive + RELOCATION_OVERHEAD_MINUTES
    assert t["details"]["moveType"] == "required"

    # an assignment that never got routed still produces a usable task
    unrouted = assignments_to_tasks(
        [{**SAMPLE, "drive_time": None, "drive_distance": None}], "t"
    )[0]
    assert unrouted["estimatedTime"] is None and unrouted["distance"] is None

    print("ok")


if __name__ == "__main__":
    demo()
