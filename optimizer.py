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


def run_optimizer():

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
        and "TOTAL" not in v["name"]
        and (v["outOfServiceReason"] in INCLUDE_OOS or v["outOfServiceReason"] is None)
    ]
    print(f"[TIMING] vehicle filtering: {time.time() - t0:.2f}s")

    t0 = time.time()
    blocks = load_block_groups_cached()
    print(f"[TIMING] load_block_groups_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    chargers = get_charger_status_cached()
    print(f"[TIMING] get_charger_status_cached: {time.time() - t0:.2f}s")

    t0 = time.time()
    block_counts, _ = categorize_trip_starts_by_block(
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
    # Precompute charger scores once per block
    charger_scores = precompute_charger_scores(blocks, chargers)

    # calculates the travel time matrix for all vehicles and blocks using Valhalla API
    travel_times = build_travel_time_matrix(evies, blocks)
    # Checks where each vehicle is currently located to avoid scoring them for their current block
    vehicle_current_blocks = get_vehicle_current_blocks(evies, blocks)

    for v in evies:
        for b in blocks:
            travel_data = travel_times.get((v["name"], b["geoid"]))
            drive_time = travel_data["time_minutes"] if travel_data else None
            relocation_distance = travel_data["distance_miles"] if travel_data else None
            result = compute_score(v, b, charger_scores[b["geoid"]], drive_time)
            if result is not None:
                scores[(v["name"], b["geoid"])] = result
            else:
                skipped += 1

    print(f"[TIMING] main scoring loop: {time.time() - t0:.2f}s")

    print(f"Feasible pairs: {len(scores)} | Skipped (infeasible): {skipped}")
    print(f"Score matrix built in {round(time.time() - start, 2)}s")

    # run the MILP
    assignments = solve_milp(
        scores, evies, blocks, geoid_to_neighborhood, neighborhood_capacities
    )

    # remove same-block assignments
    assignments = [
        (v, b, score)
        for v, b, score in assignments
        if vehicle_current_blocks.get(v) != b
        and str(vehicle_current_blocks.get(v)) != "nan"
    ]

    # microlocation
    pods = load_pods_cached(
        "data_files/Trip_History(Jan2025_Jun26).csv",
        "data_files/block_groups_with_demand.geojson",
        chargers,
    )
    micro_scores = load_micro_scores_cached(pods)
    amenity_data = fetch_all_amenities(pods)

    from collections import Counter

    amenity_types = Counter(a["type"] for a in amenity_data)
    print("\nAmenity types in service area:")
    for amenity_type, count in sorted(
        amenity_types.items(), key=lambda x: x[1], reverse=True
    ):
        print(f"  {amenity_type}: {count}")

    print("\nFinal optimized assignments:")
    evie_lookup = {ve["name"]: ve for ve in evies}

    for v, b, score in sorted(assignments, key=lambda x: x[2], reverse=True):
        block = next((bl for bl in blocks if bl["geoid"] == b), None)
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
        vehicle = evie_lookup.get(v, {})
        current_lat = vehicle.get("location", {}).get("latitude")
        current_lon = vehicle.get("location", {}).get("longitude")
        current_maps = (
            f"https://www.google.com/maps?q={current_lat},{current_lon}"
            if current_lat and current_lon
            else "Unknown"
        )

        autonomy2 = vehicle.get("autonomy2", 0) or 0
        autonomy_miles = (vehicle.get("autonomy", 0) or 0) * 0.621371
        has_charger = charger_score >= 75
        from_block = vehicle_current_blocks.get(v, "Unknown")

        # charger label — most specific first
        if autonomy_miles <= 40 and has_charger:
            charger_label = (
                "Open charger nearby and vehicle is low on battery - please charge now"
            )
        elif autonomy_miles <= 40 and not has_charger:
            charger_label = "Vehicle is low on battery but no charger nearby - please relocate to a charger"
        elif autonomy2 >= 80 and has_charger:
            charger_label = "Open charger available but vehicle doesn't need charging"
        elif autonomy2 >= 80 and not has_charger:
            charger_label = "Charging not needed and no charger nearby"
        elif has_charger:
            charger_label = "Vehicle can be charged and charger is nearby"
        else:
            charger_label = "Vehicle can be charged but no charger nearby"

        # microlocation — pick best pod within this block
        pod = select_best_pod(b, pods, micro_scores)
        pod_output = format_pod_output(pod, micro_scores, amenity_data)
        # accurate route to pod location, not block centroid
        if current_lat and current_lon and pod_output["maps_link"]:
            route = get_accurate_route(
                current_lat, current_lon, pod_output["lat"], pod_output["lon"]
            )
            drive_time_display = route["time_minutes"]
            drive_distance_display = route["distance_miles"]
        else:
            drive_time_display = None
            drive_distance_display = None

        # rest of print block
        print(
            f"  {v} --> Block: {b} | score: {score} | Nbhd: {nbhd} | {demand_label} | {charger_label}"
        )
        print(f"    Current location: {current_maps}")
        print(f"    From: {from_block} → To: {b}")
        print(f"    Drop-off: {pod_output['maps_link']}")
        print(f"    Drive: {drive_time_display} min | {drive_distance_display} miles")
        print(f"    Nearby: {pod_output['nearby_summary']}")
        print(
            f"    Micro score: {pod_output['micro_score']} | Trips from this spot: {pod_output['trip_count']}"
        )

    # neighborhood summary
    from collections import Counter

    print("\nNeighborhood summary:")
    neighborhood_counts = Counter()
    for v, b, score in assignments:
        nbhd = geoid_to_neighborhood.get(b, "Unknown")
        neighborhood_counts[nbhd] += 1

    for nbhd, count in sorted(
        neighborhood_counts.items(), key=lambda x: x[1], reverse=True
    ):
        cap = neighborhood_capacities.get(nbhd, 1)
        demand_label = (
            "High demand"
            if cap == 4
            else (
                "Medium demand"
                if cap == 3
                else "Low-medium demand" if cap == 2 else "Low demand"
            )
        )
        print(f"  {nbhd} | {count} vehicle(s) | {demand_label}")

    return scores, assignments


# MILP solver function
def solve_milp(scores, evies, blocks, geoid_to_neighborhood, neighborhood_capacities):
    prob = pulp.LpProblem("Fleet_Relocation", pulp.LpMaximize)
    x = {
        (v, b): pulp.LpVariable(f"x_{v}_{b}", cat="Binary") for (v, b) in scores.keys()
    }

    prob += pulp.lpSum(scores[(v, b)] * x[(v, b)] for (v, b) in x)

    # constraint 1: each vehicle assigned to at most one block
    vehicle_names = {v["name"] for v in evies}
    for v in vehicle_names:
        prob += pulp.lpSum(x[(v, b)] for (vv, b) in x if vv == v) <= 1

    # constraint 2: each block receives at most 2 vehicles (will try and make this more dynamic based on demand tiers on a block level)
    block_geoids = {b["geoid"] for b in blocks}
    for b in block_geoids:
        prob += pulp.lpSum(x[(v, bb)] for (v, bb) in x if bb == b) <= 2

    # constraint 3: neighborhood-level vehicle cap based on demand tier
    blocks_by_neighborhood = {}
    for b in blocks:
        nbhd = geoid_to_neighborhood.get(b["geoid"])
        if nbhd:
            blocks_by_neighborhood.setdefault(nbhd, []).append(b["geoid"])

    for nbhd, geoids in blocks_by_neighborhood.items():
        cap = neighborhood_capacities.get(nbhd, 1)
        prob += pulp.lpSum(x[(v, b)] for (v, b) in x if b in geoids) <= cap

    prob.solve()
    assignments = [(v, b, scores[(v, b)]) for (v, b) in x if x[(v, b)].varValue == 1]
    print(f"\nStatus: {pulp.LpStatus[prob.status]}")
    print(f"Vehicles relocated: {len(assignments)}")
    return assignments


if __name__ == "__main__":
    run_optimizer()
