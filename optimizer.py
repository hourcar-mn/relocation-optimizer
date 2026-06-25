from data_fetcher import *
from charger_api import *
from scoring import *
from config import *
import pulp
import time

def run_optimizer():
    print("\nStarting optimizer run...")
    start = time.time()
    #  LOAD DATA (cached)
    all_vehicles = get_vehicles_cached()
    evies        = [v for v in all_vehicles if v["name"] is not None 
                    and v["plate"] is not None
                    and int(float(v["plate"].split("-")[0].strip())) < 500 
                    and v["outOfServiceReason"] not in ["TOTALLED", "Offboarding"]
                    and "TOTAL" not in v["name"]]    
    blocks       = load_block_groups_cached()
    chargers     = get_charger_status_cached()
   
    # attach historical trip scores onto blocks before scoring
    block_counts, _ = categorize_trip_starts_by_block(
        "Trip_History(Jan2025_Jun26).csv",
        "block_groups_with_demand.geojson",
    )
    block_counts = historical_trip_score(block_counts)
    blocks = attach_historical_trip_scores(blocks, block_counts)
    print(f"Vehicles: {len(evies)} | Blocks: {len(blocks)} | Chargers: {len(chargers)}")
    
    #  BUILD SCORE MATRIX 
    print("Computing score matrix...")
    scores = {}
    skipped = 0

    for v in evies:
        for b in blocks:
            result = compute_score(v, b, chargers)
            if result is not None:
                scores[(v["name"], b["geoid"])] = result
            else:
                skipped += 1

    print(f"Feasible pairs: {len(scores)} | Skipped (infeasible): {skipped}")
    print(f"Score matrix built in {round(time.time() - start, 2)}s")

    #  PRINT TOP 10 SCORES 
    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:100]
    print("\nTop 10 assignments:")
    for (vehicle,geoid), score in top:
        print(f"  {vehicle} --> {geoid} | score: {score}")
      
   #run the MILP
    assignments = solve_milp(scores, evies, blocks)
    print("\nFinal optimized assignments:")
    for v, b, score in sorted(assignments, key=lambda x: x[2], reverse=True):
        print(f"  {v} --> {b} | score: {score}")
    return scores, assignments

#MILP solver function
def solve_milp(scores, evies, blocks):
    prob = pulp.LpProblem("Fleet_Relocation", pulp.LpMaximize)
    # decision variables -- only created for feasible (vehicle, block) pairs
    x = {
        (v, b): pulp.LpVariable(f"x_{v}_{b}", cat="Binary")
        for (v, b) in scores.keys()
    }

    # objective: maximize total score across all assignments made
    prob += pulp.lpSum(scores[(v, b)] * x[(v, b)] for (v, b) in x)
    # constraint 1: each vehicle assigned to at most one block
    vehicle_names = {v["name"] for v in evies}
    for v in vehicle_names:
        prob += pulp.lpSum(
            x[(v, b)] for (vv, b) in x if vv == v
        ) <= 1
    # constraint 2: each block receives at most one vehicle
    block_geoids = {b["geoid"] for b in blocks}
    for b in block_geoids:
        prob += pulp.lpSum(
            x[(v, bb)] for (v, bb) in x if bb == b
        ) <= 1
    prob.solve()
    assignments = [
        (v, b, scores[(v, b)])
        for (v, b) in x
        if x[(v, b)].varValue == 1
    ]
    print(f"\nStatus: {pulp.LpStatus[prob.status]}")
    print(f"Total score: {pulp.value(prob.objective):.2f}")
    print(f"Vehicles relocated: {len(assignments)}")
    return assignments

if __name__ == "__main__":
    run_optimizer()





















# for i in range(len(load_block_groups())):
#     for j in range(len(vehicles_names())):
#     score = (get_demand_score(load_block_groups()[i]) * demand_weight + vehicle_names)
#     print(score)


# def compute_score(vehicle, pod):
    # weather_penalty = {"clear": 0, "rain": 5, "cold": 10, "snow": 20}
    #this maybe used later ^ 

    # battery_level = battery[vehicle]

# will need to normalize values to be more comparable. 
    # score = (get_demand_score(load_block_groups()[i]) * demand_weight)
             # +
    #     has_charger[pod] * charger_weight * 100 +
    #     pod_vacancy[pod]     * pod_vacancy_weight +
    #     trip_rate[pod]   * trip_rate_weight * 10 +
    #     battery_level     * battery_weight +
    #     micro[pod]       * micro_location_weight+
    #     - relo_cost[vehicle][pod] * relo_cost_weight
    # ) #- weather_penalty[weather]

    # return round(score, 2)

#list of scores for each vehicle-pod pair.
# scores = {
#     (i, j): compute_score(i, j)
#     for i in vehicles
#     for j in pods
# }