"""Orchestrates a full relocation run end to end. This is the entry point
api_server.py's /assignments endpoint calls.

Pulls cached inputs from R2 (a no op locally), filters vehicles down to ones
that have been sitting long enough to relocate, builds the vehicle to block
score matrix from core_functions/scoring.py and the travel time matrix from
apis/open_street_map.py, then solves core_functions/milp_solver.solve_milp,
a MILP (via pulp) that maximizes total score subject to one relocation per
vehicle, a capacity cap per neighborhood by demand tier, and charger plug
capacity for vehicles that need charging. Each resulting block assignment is
then turned into a priced, pod-specific task by
core_functions/assignment_builder.build_assignments_output.
"""

import os
import time
import logging
from collections import Counter
from core_functions.r2_sync import pull_from_r2, push_to_r2, is_configured
from core_functions.data_fetcher import (
    get_vehicles_cached,
    get_charger_status_cached,
    load_block_groups_cached,
    trip_counts_from_blocks,
    attach_historical_trip_scores,
    attach_demand_percentiles,
    get_vehicle_current_blocks,
    BLOCK_GROUPS_GEOJSON,
    SCRIPT_DIR,
)
from core_functions.scoring import (
    historical_trip_score,
    compute_score,
    build_score_weights,
)
from core_functions.neighborhoods import (
    assign_blocks_to_neighborhoods,
    assign_points_to_neighborhoods,
    get_neighborhood_capacity_map,
)
from core_functions.charger_scoring import precompute_charger_scores
from core_functions.milp_solver import solve_milp
from core_functions.assignment_builder import build_assignments_output
from core_functions.config import (
    RECOMMENDED_MOVE_SITTING_HOURS,
    LOW_DEMAND_SITTING_HOURS,
    MAX_RELOCATION_RADIUS_MILES,
    haversine,
    hours_sitting,
    fleet_number,
)
from apis.open_street_map import build_travel_time_matrix
from microlocation.pod_generator import load_pods_cached, POD_CACHE, POD_CACHE_TTL
from microlocation.micro_scorer import load_micro_scores_cached, fetch_all_amenities

log = logging.getLogger(__name__)

REQUIRED_DATA_FILES = [
    ("data_files/trip_history.csv", "Trip history export (used for pod locations), from refresh_data.py"),
    ("data_files/member_addresses.csv", "Member addresses (lat/lon), from refresh_data.py"),
    (BLOCK_GROUPS_GEOJSON, "Block groups with demand data"),
    ("data_files/neighborhoods_edited_with_demand.geojson", "Neighborhoods geojson"),
]


def check_required_data_files():
    # fails fast with a clear message instead of an obscure crash mid-run
    required = REQUIRED_DATA_FILES

    # The trip history CSV is only read when the pod cache needs rebuilding, and
    # in the container it's pulled from R2 at that moment — so don't demand it
    # up front unless this run could actually need it and couldn't fetch it.
    pods_cached = POD_CACHE.exists() and (
        time.time() - POD_CACHE.stat().st_mtime < POD_CACHE_TTL
    )
    if pods_cached or is_configured():
        required = [(p, d) for p, d in required if "trip_history" not in p]

    missing = [
        (p, d) for p, d in required if not os.path.exists(os.path.join(SCRIPT_DIR, p))
    ]
    if missing:
        log.error("Missing required data file(s):")
        for p, d in missing:
            log.error(f"  - {d}: expected at {os.path.join(SCRIPT_DIR, p)}")
        log.error("See README.md for setup instructions.")
        raise FileNotFoundError(f"Missing {len(missing)} required data file(s).")


def _eligible_vehicles(all_vehicles):
    return [
        v
        for v in all_vehicles
        if v["name"] is not None
        and "TOTAL" not in v["name"]
        and not v.get("disabled", False)
        # a handful of test/placeholder rows (e.g. name "628noplate") aren't
        # real fleet vehicles — skip rather than crash the whole run
        and fleet_number(v["name"], " ") is not None
        and fleet_number(v["name"], " ") < 500
        and v.get("orderId") is None
        and v.get("userId") is None
    ]


def _build_score_matrix(evies, blocks, charger_scores, travel_times, block_id_to_neighborhood, neighborhood_occupancy, score_weights):
    # Haversine is a cheap first pass to skip obviously-too-far blocks before
    # scoring; it's straight-line to the block centroid, so it runs wide on
    # purpose. The real cap below checks routed road distance (already computed
    # in travel_times for every pair), which is what actually bounds how far a
    # vehicle gets sent.
    HAVERSINE_PREFILTER_MILES = 8
    t0 = time.time()
    scores = {}
    skipped = 0
    for v in evies:
        v_lat = v["location"]["latitude"]
        v_lon = v["location"]["longitude"]
        nearby_blocks = [
            b
            for b in blocks
            if haversine(v_lat, v_lon, b["lat"], b["lon"]) <= HAVERSINE_PREFILTER_MILES
        ]
        for b in nearby_blocks:
            travel_data = travel_times.get((v["name"], b["block_id"]))
            drive_time = travel_data["time_minutes"] if travel_data else None
            drive_distance = travel_data["distance_miles"] if travel_data else None
            # only enforced when we have a real routed distance -- if routing
            # failed for this pair, fall through to compute_score, which
            # estimates from haversine instead (see get_relo_cost_score)
            if drive_distance is not None and drive_distance > MAX_RELOCATION_RADIUS_MILES:
                skipped += 1
                continue
            dest_nbhd_count = neighborhood_occupancy.get(block_id_to_neighborhood.get(b["block_id"]), 0)
            result = compute_score(
                v, b, charger_scores[b["block_id"]], drive_time, score_weights, dest_nbhd_count
            )
            if result is not None:
                scores[(v["name"], b["block_id"])] = result
            else:
                skipped += 1
    print(f"[TIMING] main scoring loop: {time.time() - t0:.2f}s")
    return scores, skipped


def run_optimizer(sitting_threshold_hours=RECOMMENDED_MOVE_SITTING_HOURS, weights=None):
    pull_from_r2()  # no-op locally; restores inputs + caches on a fresh container
    check_required_data_files()

    weights = weights or {}
    score_weights = build_score_weights(
        demand=weights.get("demand"),
        charger=weights.get("charger"),
        battery=weights.get("battery"),
        annual_demand=weights.get("annual_demand"),
        relo_cost=weights.get("relo_cost"),
    )

    print("\nStarting optimizer run...")
    start = time.time()

    t0 = time.time()
    all_vehicles = get_vehicles_cached()
    print(f"[TIMING] get_vehicles_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    evies = _eligible_vehicles(all_vehicles)

    # every parked (not disabled, not on a trip) vehicle counts toward neighborhood
    # occupancy, not just the ones old enough to be relocation candidates below
    parked_vehicles = evies

    # drop vehicles that haven't been sitting long enough to be relocation
    # candidates. Use the lower bound here — the low-demand-neighborhood
    # trigger (checked later, once block demand data is loaded) can qualify a
    # vehicle before it hits the normal sitting_threshold_hours.
    evies = [
        v
        for v in evies
        if hours_sitting(v) >= min(sitting_threshold_hours, LOW_DEMAND_SITTING_HOURS)
    ]
    print(f"[TIMING] vehicle filtering: {time.time() - t0:.2f}s")

    t0 = time.time()
    blocks = load_block_groups_cached()
    print(f"[TIMING] load_block_groups_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    chargers = get_charger_status_cached()
    print(f"[TIMING] get_charger_status_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    block_counts = trip_counts_from_blocks(blocks)
    print(f"[TIMING] trip_counts_from_blocks: {time.time() - t0:.2f}s")

    t0 = time.time()
    block_counts = historical_trip_score(block_counts)
    blocks = attach_historical_trip_scores(blocks, block_counts)
    print(f"[TIMING] historical_trip_score + attach: {time.time() - t0:.2f}s")

    t0 = time.time()
    blocks = attach_demand_percentiles(blocks)
    print(f"[TIMING] attach_demand_percentiles: {time.time() - t0:.2f}s")

    block_id_to_neighborhood = assign_blocks_to_neighborhoods(
        blocks, "data_files/neighborhoods_edited_with_demand.geojson"
    )
    neighborhood_capacities = get_neighborhood_capacity_map(
        blocks, block_id_to_neighborhood
    )

    # a vehicle can sit near a block group's edge, so the group's centroid
    # (INTPTLAT/INTPTLON) can land in a different neighborhood than the
    # vehicle itself — same reasoning as pods below, look the vehicle's own
    # lat/lon up rather than reusing block_id_to_neighborhood.
    vehicle_id_to_neighborhood = assign_points_to_neighborhoods(
        [
            {"name": v["name"], "lat": v["location"]["latitude"], "lon": v["location"]["longitude"]}
            for v in evies
        ],
        "data_files/neighborhoods_edited_with_demand.geojson",
        id_key="name",
    )
    parked_vehicle_blocks = get_vehicle_current_blocks(parked_vehicles, blocks)
    neighborhood_occupancy = Counter(
        block_id_to_neighborhood.get(b) for b in parked_vehicle_blocks.values()
    )
    print(f"Vehicles: {len(evies)} | Blocks: {len(blocks)} | Chargers: {len(chargers)}")

    print("Computing score matrix...")
    t0 = time.time()
    charger_scores, charger_drive_times = precompute_charger_scores(blocks, chargers)
    print(f"[TIMING] precompute_charger_scores: {time.time() - t0:.2f}s")

    travel_times = build_travel_time_matrix(evies, blocks)
    vehicle_current_blocks = get_vehicle_current_blocks(evies, blocks)
    vehicle_current_neighborhood = {
        v: block_id_to_neighborhood.get(b) for v, b in vehicle_current_blocks.items()
    }

    scores, skipped = _build_score_matrix(
        evies, blocks, charger_scores, travel_times, block_id_to_neighborhood,
        neighborhood_occupancy, score_weights,
    )
    print(f"Feasible pairs: {len(scores)} | Skipped: {skipped}")
    print(f"Score matrix built in {round(time.time() - start, 2)}s")

    t0 = time.time()
    assignments = solve_milp(
        scores,
        evies,
        blocks,
        block_id_to_neighborhood,
        neighborhood_capacities,
        chargers,
        neighborhood_occupancy,
        vehicle_current_neighborhood,
    )
    print(f"[TIMING] solve_milp: {time.time() - t0:.2f}s")

    # drop assignments that don't actually relocate the vehicle
    assignments = [
        (v, b, score)
        for v, b, score in assignments
        if vehicle_current_blocks.get(v) != b
        and str(vehicle_current_blocks.get(v)) != "nan"
    ]

    t0 = time.time()
    pods = load_pods_cached(
        "data_files/trip_history.csv",
        BLOCK_GROUPS_GEOJSON,
        chargers,
    )
    print(f"[TIMING] load_pods_cached: {time.time() - t0:.2f}s")

    # pods can sit near a block group's edge, so the group's centroid can
    # land in a different neighborhood polygon than the pod itself — look
    # the pod's own lat/lon up rather than reusing block_id_to_neighborhood.
    pod_id_to_neighborhood = assign_points_to_neighborhoods(
        pods, "data_files/neighborhoods_edited_with_demand.geojson", id_key="pod_id"
    )

    # fetch amenities ONCE and reuse for both micro scoring and the summary
    # print below — previously fetched twice (once inside load_micro_scores_cached,
    # once again here), doubling a ~45s Overpass call on every cold run.
    t0 = time.time()
    amenity_data = fetch_all_amenities(pods)
    print(f"[TIMING] fetch_all_amenities: {time.time() - t0:.2f}s")

    t0 = time.time()
    micro_scores = load_micro_scores_cached(pods, amenity_data=amenity_data)
    print(f"[TIMING] load_micro_scores_cached: {time.time() - t0:.2f}s")

    amenity_types = Counter(a["type"] for a in amenity_data)
    print("\nAmenity types in service area:")
    for t, count in sorted(amenity_types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {t}: {count}")

    output = build_assignments_output(
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
    )

    push_to_r2()  # no-op locally; keeps caches alive across container restarts
    print(f"\n[TIMING] TOTAL optimizer run time: {time.time() - start:.2f}s")
    return scores, output


if __name__ == "__main__":
    run_optimizer()
