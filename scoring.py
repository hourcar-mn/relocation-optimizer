from data_fetcher import *
from vulog_api import *
from charger_api import *
from config import *

evies = vehicles_data()
#print(json.dumps(evies[0], indent=2))

# Compute demand score for a block group based on current conditions from the geojson properties.
def get_demand_score(block):
    day_type, season, time_window = get_conditions()
    props = block["properties"]
    full_key = f"Full demand: {day_type}, {season}, {time_window}"
    lost_key = f"Lost demand: {day_type}, {season}, {time_window}"
    full_demand = props.get(full_key, 0.0) * 100
    lost_demand = props.get(lost_key, 0.0) * 100
    combined = (full_demand * 0.6) + (lost_demand * 0.4)
    return round(min(combined, 100), 2)

def get_relo_cost_score(vehicle, block):
    v_lat = vehicle["location"]["latitude"]
    v_lon = vehicle["location"]["longitude"]
    b_lat = block["lat"]
    b_lon = block["lon"]
    #calculate disgtance in miles using haversine. 
    distance_miles = haversine(v_lat, v_lon, b_lat, b_lon)
    autonomy_miles = vehicle["autonomy"] * 0.621371 #convert km to miles. 
    # will skip this pair if vehicle cannot reach
    if autonomy_miles < distance_miles:
        return None
    # normalize to 0-100 penalty wit cap at 10 miles
    penalty = min(distance_miles / 10 * 100, 100)
    return round(penalty, 2)

def get_charger_score(block, chargers):
    b_lat = block["lat"]
    b_lon = block["lon"]
    #filter chargers to only those with availability
    available_chargers = [c for c in chargers if c["has_available"]]
    if not available_chargers:
        return 0.0
    #checks for nearest charger and gives score based on distance. 
    nearest_dist = min(
        haversine(b_lat, b_lon, c["lat"], c["lon"])
        for c in available_chargers
    )
    #if nearest charger is within 1 mile
    # score 100 if within 1 mile, linearly decreasing to 0 at 1 mile or more.
    if nearest_dist <= 1:
        return round(100 - (nearest_dist / 1 * 100), 2)
    return 0.0

def get_autonomy_score(vehicle):
    autonomy_miles = vehicle["autonomy"] * 0.621371 #convert km to miles. 
    # score is autonomy in miles, normalized to 0-100 with cap at 40 miles.
    score = min(autonomy_miles / 40 * 100, 100)
    return round(score, 2)


def compute_score(vehicle, block, chargers):
    # relocation cost — also does feasibility check
    relo_score = get_relo_cost_score(vehicle, block)
    # if vehicle cannot reach this block group skip it
    if relo_score is None:
        return None
    # all other scores
    demand_score   = get_demand_score(block)
    charger_score  = get_charger_score(block, chargers)
    autonomy_score = get_autonomy_score(vehicle)
    # weighted sum
    score = (
        demand_score   * DEMAND_WEIGHT   +
        charger_score  * CHARGER_WEIGHT  +
        autonomy_score * BATTERY_WEIGHT  -
        relo_score     * RELO_COST_WEIGHT
    )
    return round(score, 2)