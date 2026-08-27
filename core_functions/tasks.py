"""Convert optimizer assignments into the task shape the demand-dashboard task
list consumes (public/tasks.js in hourcar-mn/demand-dashboard).

The dashboard reads a small set of top-level fields (id, type, priority,
vehicle, destination, distance, estimatedTime, status). Everything the
optimizer knows that the dashboard doesn't have a field for yet is passed
through under "details" so it can be surfaced there without another change
here.
"""

from core_functions.config import RELOCATION_OVERHEAD_MINUTES

# bump when a field is renamed or removed so the dashboard can detect a mismatch
TASK_SCHEMA_VERSION = 1


def assignment_to_task(assignment, generated_at):
    a = assignment
    drive_time = a.get("drive_time")
    # compute_score sums 0-100 sub-scores against weights that total 1.0, so the
    # raw score is already on a 0-100 scale -- rescale to the 0-1 priority the
    # dashboard's own charging tasks use, so a merged queue sorts sensibly.
    score = a.get("score") or 0.0
    priority = round(min(max(score / 100.0, 0.0), 1.0), 4)

    return {
        # stable across runs on purpose — the MILP assigns each vehicle at most
        # one relocation, so this is unique within a run, and a dashboard that
        # re-polls keeps its selection instead of losing it every refresh
        "id": f"relocate-{a.get('vehicle_id') or a.get('vehicle')}",
        "type": "relocate",
        "status": "pending",
        "createdAt": generated_at,
        "source": "relocation-optimizer",
        "schemaVersion": TASK_SCHEMA_VERSION,
        "priority": priority,
        "urgent": bool(a.get("is_urgent")),
        "vehicle": {
            "id": a.get("vehicle_id"),
            "name": a.get("vehicle"),
            "plate": a.get("plate"),
            "location": {
                "latitude": a.get("current_lat"),
                "longitude": a.get("current_lon"),
            },
            "battery": {
                "percentage": a.get("autonomy2_pct"),
                "rangeMiles": a.get("autonomy_miles"),
            },
            "hoursSitting": a.get("hours_sitting"),
            "mapsLink": a.get("current_maps_link"),
        },
        "destination": {
            "latitude": a.get("drop_off_lat"),
            "longitude": a.get("drop_off_lon"),
            "name": a.get("neighborhood"),
            "blockId": a.get("block"),
            "mapsLink": a.get("drop_off_maps_link"),
            "nearbySummary": a.get("nearby_summary"),
            "microScore": a.get("micro_score"),
            "tripCount": a.get("trip_count"),
        },
        # vehicle -> drop-off pod, road distance/time from the routing API
        "distance": a.get("drive_distance"),
        "estimatedTime": (
            round(drive_time + RELOCATION_OVERHEAD_MINUTES)
            if drive_time is not None
            else None
        ),
        "details": {
            "score": a.get("score"),
            "scoreBreakdown": a.get("score_breakdown"),
            "demandLabel": a.get("demand_label"),
            "chargerLabel": a.get("charger_label"),
            "chargerDriveMinutes": a.get("charger_drive_minutes"),
            "moveType": "required" if a.get("is_required") else "recommended",
            "fromBlock": a.get("from_block"),
            "fromNeighborhood": a.get("origin_neighborhood"),
            "relocationCostUsd": a.get("relocation_cost_usd"),
            "netValueUsd": a.get("net_value_usd"),
            "worthIt": a.get("worth_it"),
            "valueEstimated": a.get("value_estimated"),
            "coldWeatherFactor": a.get("cold_weather_factor"),
        },
    }


def assignments_to_tasks(assignments, generated_at):
    return sorted(
        (assignment_to_task(a, generated_at) for a in assignments),
        key=lambda t: t["priority"],
        reverse=True,
    )
