from core_functions.data_fetcher import *
from apis.charger_api import *
from core_functions.scoring import *
from core_functions.config import *
import pulp
import time
from apis.open_street_map import *
from microlocation.pod_generator import load_pods_cached
from microlocation.micro_scorer import load_micro_scores_cached, fetch_all_amenities
from microlocation.pod_selector import select_best_pod, format_pod_output
import math
from collections import Counter
import concurrent.futures
import pickle
from datetime import datetime, timezone
import numpy as np

_POD_ROUTE_CACHE_PATH = os.path.join(SCRIPT_DIR, "data_files", "_pod_route_cache.pkl")

REQUIRED_DATA_FILES = [
    ("data_files/Trip_History(Jan2025_Jun26).csv", "Trip history export"),
    ("data_files/member_addresses.csv", "Member addresses (lat/lon)"),
    ("data_files/block_groups_with_demand.geojson", "Block groups with demand data"),
    ("data_files/neighborhoods_edited_with_demand.geojson", "Neighborhoods geojson"),
]


def check_required_data_files():
    # fails fast with a clear message instead of an obscure crash mid-run
    missing = [
        (p, d)
        for p, d in REQUIRED_DATA_FILES
        if not os.path.exists(os.path.join(SCRIPT_DIR, p))
    ]
    if missing:
        print("\nMissing required data file(s):")
        for p, d in missing:
            print(f"  - {d}: expected at {os.path.join(SCRIPT_DIR, p)}")
        print("See README.md for setup instructions.\n")
        raise FileNotFoundError(f"Missing {len(missing)} required data file(s).")


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


def _load_pod_route_cache():
    if os.path.exists(_POD_ROUTE_CACHE_PATH):
        try:
            with open(_POD_ROUTE_CACHE_PATH, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return {}


def _save_pod_route_cache(cache):
    try:
        os.makedirs(os.path.dirname(_POD_ROUTE_CACHE_PATH), exist_ok=True)
        with open(_POD_ROUTE_CACHE_PATH, "wb") as f:
            pickle.dump(cache, f)
    except Exception as e:
        print(f"Failed to write pod route cache: {e}")


def run_optimizer(sitting_threshold_hours=SOFT_MOVE_SITTING_HOURS):
    check_required_data_files()

    print("\nStarting optimizer run...")
    start = time.time()

    t0 = time.time()
    all_vehicles = get_vehicles_cached()
    print(f"[TIMING] get_vehicles_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    evies = [
        v
        for v in all_vehicles
        if v["name"] is not None
        and v["plate"] is not None
        and int(float(v["plate"].split("-")[0].strip())) < 500
        and v["outOfServiceReason"] not in ["TOTALLED", "Offboarding"]
        and "TOTAL" not in v["name"]
        and v.get("booking_status") == 0
        and v.get("orderId") is None
        and v.get("userId") is None
    ]
    print(f"[TIMING] vehicle filtering: {time.time() - t0:.2f}s")

    t0 = time.time()
    blocks = load_block_groups_cached()
    print(f"[TIMING] load_block_groups_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    chargers = get_charger_status_cached()
    print(f"[TIMING] get_charger_status_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    block_counts, _ = categorize_trip_starts_by_block_cached(
        "data_files/Trip_History(Jan2025_Jun26).csv",
        "data_files/block_groups_with_demand.geojson",
    )
    print(f"[TIMING] categorize_trip_starts_by_block: {time.time() - t0:.2f}s")

    t0 = time.time()
    block_counts = historical_trip_score(block_counts)
    blocks = attach_historical_trip_scores(blocks, block_counts)
    print(f"[TIMING] historical_trip_score + attach: {time.time() - t0:.2f}s")

    t0 = time.time()
    blocks = attach_demand_percentiles(blocks)
    print(f"[TIMING] attach_demand_percentiles: {time.time() - t0:.2f}s")

    geoid_to_neighborhood = assign_blocks_to_neighborhoods(
        blocks, "data_files/neighborhoods_edited_with_demand.geojson"
    )
    neighborhood_capacities = get_neighborhood_capacity_map(
        blocks, geoid_to_neighborhood
    )
    print(f"Vehicles: {len(evies)} | Blocks: {len(blocks)} | Chargers: {len(chargers)}")

    print("Computing score matrix...")
    scores = {}
    skipped = 0

    t0 = time.time()
    charger_scores, charger_drive_times = precompute_charger_scores(blocks, chargers)
    print(f"[TIMING] precompute_charger_scores: {time.time() - t0:.2f}s")

    t0 = time.time()
    travel_times = build_travel_time_matrix(evies, blocks)
    vehicle_current_blocks = get_vehicle_current_blocks(evies, blocks)

    for v in evies:
        for b in blocks:
            travel_data = travel_times.get((v["name"], b["geoid"]))
            drive_time = travel_data["time_minutes"] if travel_data else None
            result = compute_score(v, b, charger_scores[b["geoid"]], drive_time)
            if result is not None:
                scores[(v["name"], b["geoid"])] = result
            else:
                skipped += 1
    print(f"[TIMING] main scoring loop: {time.time() - t0:.2f}s")

    print(f"Feasible pairs: {len(scores)} | Skipped: {skipped}")
    print(f"Score matrix built in {round(time.time() - start, 2)}s")

    assignments = solve_milp(
        scores, evies, blocks, geoid_to_neighborhood, neighborhood_capacities, chargers
    )

    # drop assignments that don't actually relocate the vehicle
    assignments = [
        (v, b, score)
        for v, b, score in assignments
        if vehicle_current_blocks.get(v) != b
        and str(vehicle_current_blocks.get(v)) != "nan"
    ]

    pods = load_pods_cached(
        "data_files/Trip_History(Jan2025_Jun26).csv",
        "data_files/block_groups_with_demand.geojson",
        chargers,
    )
    micro_scores = load_micro_scores_cached(pods)
    amenity_data = fetch_all_amenities(pods)

    amenity_types = Counter(a["type"] for a in amenity_data)
    print("\nAmenity types in service area:")
    for t, count in sorted(amenity_types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {t}: {count}")

    print("\nFinal optimized assignments:")
    evie_lookup = {ve["name"]: ve for ve in evies}
    block_lookup = {bl["geoid"]: bl for bl in blocks}

    # pass 1: everything except routing calls
    t0 = time.time()
    prelim = []
    skipped_no_pod = 0
    skipped_not_sitting = 0
    for v, b, score in sorted(assignments, key=lambda x: x[2], reverse=True):
        block = block_lookup.get(b)
        nbhd = geoid_to_neighborhood.get(b, "Unknown")
        demand_score = get_demand_score(block) if block else 0
        demand_category = get_neighborhood_capacity(demand_score)
        demand_label = (
            "High demand"
            if demand_category == 4
            else (
                "Medium demand"
                if demand_category == 3
                else "Low-medium demand" if demand_category == 2 else "Low demand"
            )
        )
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

        last_moving = vehicle.get("location", {}).get("lastMovingDate")
        hours_sitting = 0.0
        if last_moving:
            try:
                last_moving_dt = datetime.fromisoformat(
                    last_moving.replace("Z", "+00:00")
                )
                hours_sitting = (
                    datetime.now(timezone.utc) - last_moving_dt
                ).total_seconds() / 3600
            except (ValueError, TypeError):
                hours_sitting = 0.0

        is_hard = hours_sitting >= HARD_MOVE_SITTING_HOURS
        is_soft = sitting_threshold_hours <= hours_sitting < HARD_MOVE_SITTING_HOURS
        is_urgent = is_hard

        if not is_hard and not is_soft:
            # sitting less than the soft threshold — not a relocation candidate
            skipped_not_sitting += 1
            continue

        if autonomy_miles <= 40 and has_charger:
            charger_label = (
                "Open charger nearby — vehicle is low on battery, please charge now"
            )
        elif autonomy_miles <= 40 and not has_charger:
            charger_label = "Vehicle is low on battery but no open charger nearby"
        elif autonomy2 >= 80 and has_charger:
            charger_label = "Open charger available but vehicle doesn't need charging"
        elif autonomy2 >= 80 and not has_charger:
            charger_label = "Charging not needed and no open charger nearby"
        elif has_charger:
            charger_label = "Vehicle can be charged — open charger nearby"
        else:
            charger_label = "Vehicle can be charged but no open charger nearby"

        pod = select_best_pod(b, pods, micro_scores, vehicle)
        if pod is None:
            # no pod in this block clears the 1-mile minimum (or the block has
            # no pods at all) — there's no real drop-off location to recommend
            skipped_no_pod += 1
            continue
        pod_output = format_pod_output(pod, micro_scores, amenity_data)

        # recompute sub-scores for the frontend breakdown, mirrors compute_score()
        travel_data = travel_times.get((v, b))
        relo_drive_time = travel_data["time_minutes"] if travel_data else None
        relo_score = (
            get_relo_cost_score(vehicle, block, relo_drive_time) if block else None
        )
        autonomy_score = get_autonomy_score(vehicle)
        historical_score = (
            block["properties"].get("historical_trip_score", 0.0) if block else 0.0
        )

        score_breakdown = {
            "demand": round(demand_score, 2),
            "demand_weight": DEMAND_WEIGHT,
            "charger": round(charger_score, 2),
            "charger_weight": CHARGER_WEIGHT,
            "autonomy": round(autonomy_score, 2),
            "autonomy_weight": BATTERY_WEIGHT,
            "trip_rate": round(historical_score, 2),
            "trip_rate_weight": TRIP_RATE_WEIGHT,
            "relocation_cost": round(relo_score, 2) if relo_score is not None else None,
            "relocation_cost_weight": RELO_COST_WEIGHT,
        }

        prelim.append(
            {
                "vehicle": v,
                "block": b,
                "score": score,
                "score_breakdown": score_breakdown,
                "neighborhood": nbhd,
                "demand_label": demand_label,
                "charger_label": charger_label,
                "is_hard": is_hard,
                "is_soft": is_soft,
                "is_urgent": is_urgent,
                "autonomy_miles": round(autonomy_miles, 1),
                "autonomy2_pct": round(autonomy2, 1),
                "hours_sitting": round(hours_sitting, 1),
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
                "drive_time": None,
                "drive_distance": None,
            }
        )
    print(f"[TIMING] build prelim assignment data: {time.time() - t0:.2f}s")
    if skipped_no_pod:
        print(
            f"Skipped {skipped_no_pod} assignment(s): no pod cleared the 1-mile minimum"
        )

    # pass 2: route vehicle -> pod concurrently, cached by rounded coords
    t0 = time.time()
    pod_route_cache = _load_pod_route_cache()
    cache_hits = 0

    def _cache_key(item):
        if not (
            item["current_lat"]
            and item["current_lon"]
            and item["drop_off_lat"]
            and item["drop_off_lon"]
        ):
            return None
        return (
            round(item["current_lat"], 5),
            round(item["current_lon"], 5),
            round(item["drop_off_lat"], 5),
            round(item["drop_off_lon"], 5),
        )

    items_needing_routing = []
    for item in prelim:
        key = _cache_key(item)
        if key is not None and key in pod_route_cache:
            item["drive_time"], item["drive_distance"] = pod_route_cache[key]
            cache_hits += 1
        elif key is not None and item["drop_off_maps_link"]:
            items_needing_routing.append(item)

    def _route_for_item(item):
        try:
            route = get_accurate_route(
                item["current_lat"],
                item["current_lon"],
                item["drop_off_lat"],
                item["drop_off_lon"],
            )
            return route["time_minutes"], route["distance_miles"]
        except Exception:
            return None, None

    if items_needing_routing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(_route_for_item, items_needing_routing))

        for item, (drive_time, drive_distance) in zip(items_needing_routing, results):
            item["drive_time"] = drive_time
            item["drive_distance"] = drive_distance
            key = _cache_key(item)
            if key is not None and drive_time is not None:
                pod_route_cache[key] = (drive_time, drive_distance)

        _save_pod_route_cache(pod_route_cache)

    print(
        f"[TIMING] vehicle-to-pod routing: {time.time() - t0:.2f}s ({cache_hits} cached, {len(items_needing_routing)} routed)"
    )

    # cost = labor (2 fleet members, prorated for drive time + overhead) + charge used.
    # charge cost per mile goes up in cold weather, since EVs get fewer miles per
    # charge in the cold -- same battery, less distance, so each mile costs more.
    current_temp_f = get_current_temp_f()

    # TODO: replace with real avg revenue/trip once pulled from SQL billing data tonight.
    # Placeholder estimate — NOT yet calibrated against real HourCar numbers.
    # TODO: replace with real slope from regressing trip counts against demand
    # scores across blocks, once calibrated.
    REVENUE_PER_DEMAND_POINT_PER_DAY = 0.75  # placeholder
    AVG_TRIPS_PER_VEHICLE_PER_DAY = 1.90  # free-float, from Overall Dashboard
    AVG_REVENUE_PER_TRIP = 14.00          # free-float specific, from Revenue Details
    REVENUE_PER_DEMAND_POINT_PER_DAY = (AVG_TRIPS_PER_VEHICLE_PER_DAY / 100) * AVG_REVENUE_PER_TRIP
# = (1.90 / 100) * 14.00
# = 0.019 * 14.00
# ≈ $0.266 per demand point per day
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
            expected_added_daily_revenue = (
                demand_score * REVENUE_PER_DEMAND_POINT_PER_DAY
            )
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

    output = sanitize_deep(prelim)
    return scores, output


def solve_milp(
    scores, evies, blocks, geoid_to_neighborhood, neighborhood_capacities, chargers
):
    prob = pulp.LpProblem("Fleet_Relocation", pulp.LpMaximize)
    x = {
        (v, b): pulp.LpVariable(f"x_{v}_{b}", cat="Binary") for (v, b) in scores.keys()
    }
    prob += pulp.lpSum(scores[(v, b)] * x[(v, b)] for (v, b) in x)

    # each vehicle assigned at most once
    vehicle_names = {v["name"] for v in evies}
    for v in vehicle_names:
        prob += pulp.lpSum(x[(v, b)] for (vv, b) in x if vv == v) <= 1

    # neighborhood-level cap by demand tier
    blocks_by_neighborhood = {}
    for b in blocks:
        nbhd = geoid_to_neighborhood.get(b["geoid"])
        if nbhd:
            blocks_by_neighborhood.setdefault(nbhd, []).append(b["geoid"])
    for nbhd, geoids in blocks_by_neighborhood.items():
        cap = neighborhood_capacities.get(nbhd, 1)
        prob += pulp.lpSum(x[(v, b)] for (v, b) in x if b in geoids) <= cap

    # charger plug capacity, only for vehicles that actually need charging
    charger_capacity = {}
    for c in chargers:
        nearest_block = min(
            blocks,
            key=lambda b: (b["lat"] - c["lat"]) ** 2 + (b["lon"] - c["lon"]) ** 2,
        )
        geoid = nearest_block["geoid"]
        charger_capacity[geoid] = charger_capacity.get(geoid, 0) + c["available"]

    needs_charging = {
        v["name"] for v in evies if (v.get("autonomy", 0) or 0) * 0.621371 < 40
    }
    for b_geoid, plug_count in charger_capacity.items():
        if plug_count > 0:
            prob += (
                pulp.lpSum(
                    x[(v, bb)] for (v, bb) in x if bb == b_geoid and v in needs_charging
                )
                <= plug_count
            )

    prob.solve()
    assignments = [(v, b, scores[(v, b)]) for (v, b) in x if x[(v, b)].varValue == 1]
    print(f"\nStatus: {pulp.LpStatus[prob.status]}")
    print(f"Vehicles relocated: {len(assignments)}")
    return assignments


if __name__ == "__main__":
    run_optimizer()
