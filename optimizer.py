from data_fetcher import *
from charger_api import *
from scoring import *
from config import *
import pulp
vehicles = vehicles_data()
blocks = load_block_groups()
autonomy_dict = {v["name"]: v["autonomy"] for v in vehicles}
vehicle_location = {v["name"]: (v["location"]) for v in vehicles}
#print(vehicle_location)
#print(autonomy_dict)
#print(get_charger_status())
#print(get_relo_cost_score(vehicles[0], blocks[0]))
# v = evies[0]
# b = load_block_groups()[0]
#print("\n".join(block_group_names()))
#weights for variables
#print("\n".join(vehicle_types()))
#print(compute_score(v, b, get_charger_status_cached()))


def run_optimizer():
    print("\nStarting optimizer run...")
    start = time.time()

    #  LOAD DATA (cached)
    all_vehicles = get_vehicles_cached()
    evies        = [v for v in all_vehicles if v["name"] is not None 
                    and v["plate"] is not None
                    and int(float(v["plate"].split("-")[0].strip())) < 500]
    blocks       = load_block_groups_cached()
    chargers     = get_charger_status_cached()

    print(f"Vehicles: {len(evies)} | Blocks: {len(blocks)} | Chargers: {len(chargers)}")

    # debugging
    print("\n--- Debugging individual variable scores ---")
    sample_vehicle = evies[0]
    for b in blocks[:5]:
        demand   = get_demand_score(b)
        charger  = get_charger_score(b, chargers)
        autonomy = get_autonomy_score(sample_vehicle)
        relo     = get_relo_cost_score(sample_vehicle, b)
        print(f"{b['geoid']:25} demand={demand:7.2f}  charger={charger:7.2f}  autonomy={autonomy:7.2f}  relo={relo}")
    # end debugging

    print("\n--- Investigating the dominant block group ---")
    dominant_geoid = "270531057001"
    dominant = [b for b in blocks if b["geoid"] == dominant_geoid][0]

    print("Demand score:", get_demand_score(dominant))
    print("Charger score:", get_charger_score(dominant, chargers))
    print("Block lat/lon:", dominant["lat"], dominant["lon"])

    # Check relo cost and total score for 5 different vehicles going to this block
    for v in evies[:5]:
        relo = get_relo_cost_score(v, dominant)
        total = compute_score(v, dominant, chargers)
        print(f"{v['name']:25} relo={relo}  total_score={total}  vehicle_loc=({v['location']['latitude']}, {v['location']['longitude']})")

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

    return scores

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