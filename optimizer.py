from core_functions.data_fetcher import *
from apis.charger_api import *
from core_functions.scoring import *
from core_functions.config import *
import pulp
import time
from apis.open_street_map import *


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
    for i in range(len(evies)):
        v = evies[i]
        print(v["name"], (v["outOfServiceReason"]))

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
    print("\nFinal optimized assignments:")

    for v, b, score in sorted(assignments, key=lambda x: x[2], reverse=True):
        block = next((bl for bl in blocks if bl["geoid"] == b), None)
        nbhd = geoid_to_neighborhood.get(b, "Unknown")
        nbhd_cap = neighborhood_capacities.get(nbhd, 1)
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
        going_to_charger = "Block has an open charger" if charger_score >= 75 else ""
        print(
            f"  {v} --> Block: {b} | score: {score} | Nbhd: {nbhd} | {going_to_charger}"
        )
    print("\n--- Top 5 & Bottom 5 score breakdown ---")
    sorted_assignments = sorted(assignments, key=lambda x: x[2], reverse=True)
    to_breakdown = sorted_assignments[:5] + sorted_assignments[-5:]

    for v, b, total_score in to_breakdown:

        block = next((bl for bl in blocks if bl["geoid"] == b), None)

        if not block:
            continue
        demand = get_demand_score(block)
        charger = charger_scores.get(b, 0)
        autonomy_miles = next(
            (ve["autonomy"] * 0.621371 for ve in evies if ve["name"] == v), 0
        )

        battery = get_autonomy_score(next(ve for ve in evies if ve["name"] == v))
        historical = block["properties"].get("historical_trip_score", 0.0)
        relo = get_relo_cost_score(next(ve for ve in evies if ve["name"] == v), block)
        nbhd = geoid_to_neighborhood.get(b, "Unknown")

        travel_data2 = travel_times.get((v, b), None)
        drive_time = travel_data2["time_minutes"] if travel_data2 else None
        relocation_distance = travel_data2["distance_miles"] if travel_data2 else None

        print(f"\n  {v} --> {b} | {nbhd} | total: {total_score}")
        print(
            f"    demand intensity score:         raw: {demand}  weighted: {round(demand * DEMAND_WEIGHT, 2):>6}"
        )
        print(
            f"    charger proximity score:        raw: {charger}  weighted:  {round(charger * CHARGER_WEIGHT, 2):>6}"
        )
        print(
            f"    battery  feasibility score:     raw: {battery}  weighted: {round(battery * BATTERY_WEIGHT, 2):>6}"
        )
        print(
            f"    historical trip rate score:     raw: {round(historical, 2)}  weighted: {round(historical * TRIP_RATE_WEIGHT, 2):>6}"
        )
        print(
            f"    relocation cost penalty: -      raw: {relo}  weighted: {round(relo * RELO_COST_WEIGHT, 2):>6}"
        )
        print(f"    autonomy:                       {round(autonomy_miles, 1)} miles")
        print(
            f"    average driving time for this relocation:          {drive_time} minutes"
        )
        print(f"    relocation drive distance:      {relocation_distance} miles")

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
