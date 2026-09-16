"""Turns solve_milp's raw (vehicle, block, score) assignments into the final
task list the frontend/dashboard consumes: picks a specific pod per block,
routes vehicle-to-pod for real drive time/distance (cached to disk, keyed on
rounded coordinates), and prices the move into a relocation cost weighed
against an expected net revenue value.
"""

import math
import logging
import time
import numpy as np
from core_functions.scoring import (
    get_demand_score,
    get_demand_annual_score,
    get_current_temp_f,
    get_cold_weather_factor,
    get_relo_cost_score,
    get_autonomy_score,
)
from core_functions.neighborhoods import get_neighborhood_capacity
from core_functions.pod_routing import route_prelim_to_pods
from core_functions.config import (
    MIN_AUTONOMY_LEVEL,
    MIN_BATTERY_LEVEL_FOR_CHARGING,
    REQUIRED_MOVE_SITTING_HOURS,
    LOW_DEMAND_SITTING_HOURS,
    FLEET_MEMBERS_PER_RELOCATION,
    LABOR_RATE_PER_HOUR_PER_PERSON,
    RELOCATION_OVERHEAD_MINUTES,
    CHARGE_COST_PER_MILE,
    CHARGER_NEARBY_DRIVE_MINUTES,
    hours_sitting,
)
from microlocation.pod_selector import select_best_pod, format_pod_output

log = logging.getLogger(__name__)

# TODO: replace with real avg revenue/trip once pulled from SQL billing data.
# Placeholder estimate — NOT yet calibrated against real HourCar numbers.
AVG_TRIPS_PER_VEHICLE_PER_DAY = 1.90  # free-float, from Overall Dashboard
AVG_REVENUE_PER_TRIP = 14.00  # free-float specific, from Revenue Details
REVENUE_PER_DEMAND_POINT_PER_DAY = (AVG_TRIPS_PER_VEHICLE_PER_DAY / 100) * AVG_REVENUE_PER_TRIP


def sanitize_deep(obj, _stats=None):
    top = _stats is None
    if top:
        _stats = {"replaced": 0}

    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        _stats["replaced"] += 1
        return None
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            _stats["replaced"] += 1
            return None
        return val
    if isinstance(obj, dict):
        result = {k: sanitize_deep(v, _stats) for k, v in obj.items()}
    elif isinstance(obj, list):
        result = [sanitize_deep(v, _stats) for v in obj]
    else:
        result = obj

    if top and _stats["replaced"] > 0:
        print(f"Replaced {_stats['replaced']} NaN/Inf value(s) with None.")
    return result


def _demand_label(demand_category):
    if demand_category == 4:
        return "High demand"
    if demand_category == 3:
        return "Medium demand"
    if demand_category == 2:
        return "Low-medium demand"
    return "Low demand"


def _charger_label(autonomy_miles, autonomy2, has_charger):
    if autonomy_miles <= MIN_AUTONOMY_LEVEL and has_charger:
        return "Open charger nearby — vehicle is low on battery, please charge now"
    if autonomy_miles <= MIN_AUTONOMY_LEVEL and not has_charger:
        return "Vehicle is low on battery but no open charger nearby at destination"
    if autonomy2 >= MIN_BATTERY_LEVEL_FOR_CHARGING and has_charger:
        return "Open charger available at destination but vehicle doesn't need charging"
    if autonomy2 >= MIN_BATTERY_LEVEL_FOR_CHARGING and not has_charger:
        return "Charging not needed and no open charger nearby at destination"
    if has_charger:
        return "Vehicle can be charged open charger nearby at destination"
    return "Vehicle can be charged but no open charger nearby at destination"


def _build_prelim_assignments(
    assignments,
    evie_lookup,
    block_lookup,
    pods,
    micro_scores,
    amenity_data,
    charger_scores,
    charger_drive_times,
    travel_times,
    block_id_to_neighborhood,
    pod_id_to_neighborhood,
    vehicle_id_to_neighborhood,
    vehicle_current_blocks,
    neighborhood_occupancy,
    score_weights,
    sitting_threshold_hours,
):
    prelim = []
    skipped_no_pod = 0
    used_pod_ids = set()
    for v, b, score in sorted(assignments, key=lambda x: x[2], reverse=True):
        block = block_lookup.get(b)
        nbhd = block_id_to_neighborhood.get(b, "Unknown")
        dest_nbhd_vehicle_count = neighborhood_occupancy.get(block_id_to_neighborhood.get(b), 0)
        is_fulfillment = dest_nbhd_vehicle_count == 0
        demand_score = get_demand_score(block, dest_nbhd_vehicle_count) if block else 0
        demand_category = get_neighborhood_capacity(demand_score)
        demand_label = _demand_label(demand_category)
        charger_score = charger_scores.get(b, 0)
        charger_drive_time_min = charger_drive_times.get(b)
        vehicle = evie_lookup.get(v, {})
        autonomy2 = round(float(vehicle.get("autonomy2", 0) or 0), 1)
        current_lat = vehicle.get("location", {}).get("latitude")
        current_lon = vehicle.get("location", {}).get("longitude")
        autonomy_miles = (vehicle.get("autonomy", 0) or 0) * 0.621371
        has_charger = (
            charger_drive_time_min is not None
            and charger_drive_time_min <= CHARGER_NEARBY_DRIVE_MINUTES
        )
        from_block = vehicle_current_blocks.get(v, "Unknown")
        origin_nbhd = vehicle_id_to_neighborhood.get(v, "Unknown")

        vehicle_hours_sitting = hours_sitting(vehicle)

        origin_block = block_lookup.get(from_block)
        origin_demand_score = get_demand_score(origin_block) if origin_block else 0
        is_low_demand_early = (
            vehicle_hours_sitting >= LOW_DEMAND_SITTING_HOURS
            and get_neighborhood_capacity(origin_demand_score) == 1
        )

        is_required = vehicle_hours_sitting >= REQUIRED_MOVE_SITTING_HOURS
        is_recommended = sitting_threshold_hours <= vehicle_hours_sitting < REQUIRED_MOVE_SITTING_HOURS
        is_urgent = is_required

        if not is_required and not is_recommended and not is_low_demand_early:
            # sitting less than the recommended threshold, and not a low-demand-neighborhood
            # early trigger either — not a relocation candidate
            continue

        charger_label = _charger_label(autonomy_miles, autonomy2, has_charger)

        pod = select_best_pod(b, pods, micro_scores, vehicle, exclude_pod_ids=used_pod_ids)
        if pod is None:
            # no pod in this block clears the 1-mile minimum (or the block has
            # no pods at all, or every pod is already taken by a higher-scoring
            # assignment this run) — there's no real drop-off location to recommend
            skipped_no_pod += 1
            continue
        used_pod_ids.add(pod["pod_id"])
        nbhd = pod_id_to_neighborhood.get(pod["pod_id"]) or nbhd
        pod_output = format_pod_output(pod, micro_scores, amenity_data)

        # recompute sub-scores for the frontend breakdown, mirrors compute_score()
        travel_data = travel_times.get((v, b))
        relo_drive_time = travel_data["time_minutes"] if travel_data else None
        relo_score = (
            get_relo_cost_score(vehicle, block, relo_drive_time) if block else None
        )
        autonomy_score = get_autonomy_score(vehicle)
        demand_annual_score = get_demand_annual_score(block) if block else 0.0

        score_breakdown = {
            "demand": round(demand_score, 2),
            "demand_weight": round(score_weights["demand"], 3),
            "charger": round(charger_score, 2),
            "charger_weight": round(score_weights["charger"], 3),
            "autonomy": round(autonomy_score, 2),
            "autonomy_weight": round(score_weights["battery"], 3),
            "annual_demand": round(demand_annual_score, 2),
            "annual_demand_weight": round(score_weights["annual_demand"], 3),
            "relocation_cost": round(relo_score, 2) if relo_score is not None else None,
            "relocation_cost_weight": round(score_weights["relo_cost"], 3),
        }

        prelim.append(
            {
                "vehicle": v,
                # Vulog id/plate so downstream tools (demand-dashboard) can join
                # these tasks against their own vehicle feed
                "vehicle_id": vehicle.get("id"),
                "plate": vehicle.get("plate"),
                "block": b,
                "score": score,
                "score_breakdown": score_breakdown,
                "neighborhood": nbhd,
                "origin_neighborhood": origin_nbhd,
                "demand_label": demand_label,
                "charger_label": charger_label,
                "is_required": is_required,
                "is_recommended": is_recommended,
                "is_urgent": is_urgent,
                "is_low_demand_early": is_low_demand_early,
                "is_fulfillment": is_fulfillment,
                "autonomy_miles": round(autonomy_miles, 1),
                "autonomy2_pct": round(autonomy2, 1),
                "hours_sitting": round(vehicle_hours_sitting, 1),
                "charger_drive_minutes": charger_drive_time_min,
                "from_block": from_block,
                "current_lat": current_lat,
                "current_lon": current_lon,
                "current_maps_link": (
                    f"https://www.google.com/maps?q={current_lat},{current_lon}"
                    if current_lat and current_lon
                    else None
                ),
                "drop_off_lat": pod_output.get("lat"),
                "drop_off_lon": pod_output.get("lon"),
                "drop_off_maps_link": pod_output.get("maps_link"),
                "micro_score": pod_output.get("micro_score"),
                "trip_count": pod_output.get("trip_count"),
                "nearby_summary": pod_output.get("nearby_summary"),
                "drive_time": None,
                "drive_distance": None,
            }
        )
    if skipped_no_pod:
        print(
            f"Skipped {skipped_no_pod} assignment(s): no pod cleared the 1-mile minimum"
        )
    return prelim


def _apply_cost_and_net_value(prelim):
    # cost = labor (2 fleet members, prorated for drive time + overhead) + charge used.
    # charge cost per mile goes up in cold weather, since EVs get fewer miles per
    # charge in the cold -- same battery, less distance, so each mile costs more.
    t0 = time.time()
    current_temp_f = get_current_temp_f()

    for item in prelim:
        if item["drive_time"] is not None and item["drive_distance"] is not None:
            labor_hours = (item["drive_time"] + RELOCATION_OVERHEAD_MINUTES) / 60
            labor_cost = (
                FLEET_MEMBERS_PER_RELOCATION
                * LABOR_RATE_PER_HOUR_PER_PERSON
                * labor_hours
            )

            cold_factor = get_cold_weather_factor(item["vehicle"], current_temp_f)
            cost_per_mile = (
                CHARGE_COST_PER_MILE / cold_factor
                if cold_factor > 0
                else CHARGE_COST_PER_MILE
            )
            charge_cost = item["drive_distance"] * cost_per_mile

            item["relocation_cost_usd"] = round(labor_cost + charge_cost, 2)
            item["cold_weather_factor"] = round(cold_factor, 3)

            # dollar-for-dollar: expected added revenue from moving to a higher
            # demand block, compared against what the move actually costs.
            # PLACEHOLDER revenue numbers -- pending real calibration.
            demand_score = item["score_breakdown"]["demand"]
            expected_added_daily_revenue = demand_score * REVENUE_PER_DEMAND_POINT_PER_DAY
            item["net_value_usd"] = round(
                expected_added_daily_revenue - item["relocation_cost_usd"], 2
            )
            item["worth_it"] = item["net_value_usd"] > 0
            item["value_estimated"] = True  # flags this as using placeholder numbers
        else:
            item["relocation_cost_usd"] = None
            item["cold_weather_factor"] = None
            item["net_value_usd"] = None
            item["worth_it"] = None
            item["value_estimated"] = None
    print(f"[TIMING] cost/net-value calc: {time.time() - t0:.2f}s")
    return prelim


def build_assignments_output(
    assignments,
    evies,
    blocks,
    pods,
    micro_scores,
    amenity_data,
    charger_scores,
    charger_drive_times,
    travel_times,
    block_id_to_neighborhood,
    pod_id_to_neighborhood,
    vehicle_id_to_neighborhood,
    vehicle_current_blocks,
    neighborhood_occupancy,
    score_weights,
    sitting_threshold_hours,
):
    print("\nFinal optimized assignments:")
    evie_lookup = {ve["name"]: ve for ve in evies}
    block_lookup = {bl["block_id"]: bl for bl in blocks}

    t0 = time.time()
    prelim = _build_prelim_assignments(
        assignments,
        evie_lookup,
        block_lookup,
        pods,
        micro_scores,
        amenity_data,
        charger_scores,
        charger_drive_times,
        travel_times,
        block_id_to_neighborhood,
        pod_id_to_neighborhood,
        vehicle_id_to_neighborhood,
        vehicle_current_blocks,
        neighborhood_occupancy,
        score_weights,
        sitting_threshold_hours,
    )
    print(f"[TIMING] build prelim assignment data: {time.time() - t0:.2f}s")
    prelim = route_prelim_to_pods(prelim)
    prelim = _apply_cost_and_net_value(prelim)
    return sanitize_deep(prelim)
