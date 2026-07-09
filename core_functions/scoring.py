from core_functions.data_fetcher import *
from apis.vulog_api import *
from apis.charger_api import *
from core_functions.config import *
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.geometry import Point
from collections import defaultdict

day_type, season, time_window = get_conditions()


# Compute demand score for a block group based on current conditions from the geojson properties.
def get_demand_score(block):
    props = block["properties"]
    full_key = f"Full demand: {day_type}, {season}, {time_window} (pct)"
    lost_key = f"Lost demand: {day_type}, {season}, {time_window} (pct)"
    full_demand = props.get(full_key, 0.0)
    lost_demand = props.get(lost_key, 0.0)
    combined = (full_demand * 0.6) + (lost_demand * 0.4)
    return round(combined, 2)


def get_relo_cost_score(vehicle, block, drive_time_minutes=None):
    # feasibility check regardless of routing source
    autonomy_miles = vehicle["autonomy"] * 0.621371
    v_lat = vehicle["location"]["latitude"]
    v_lon = vehicle["location"]["longitude"]
    distance_miles = haversine(v_lat, v_lon, block["lat"], block["lon"])
    if autonomy_miles < distance_miles:
        return None

    if drive_time_minutes is None:
        drive_time_minutes = (distance_miles / AVG_CITY_SPEED_MPH) * 60

    total_labor_minutes = drive_time_minutes + RELOCATION_OVERHEAD_MINUTES
    penalty = min(total_labor_minutes / 60 * 100, 100)
    return round(penalty, 2)


CHARGER_MAX_DISTANCE_MILES = 2


def get_charger_score(block, available_chargers):

    b_lat = block["lat"]
    b_lon = block["lon"]
    if not available_chargers:
        return 0.0
    # checks for nearest charger and gives score based on distance.
    nearest_dist = min(
        haversine(b_lat, b_lon, c["lat"], c["lon"]) for c in available_chargers
    )
    # if nearest charger is within 1 mile
    # score 100 if within 1 mile, linearly decreasing to 0 at 1 mile or more.
    if nearest_dist >= CHARGER_MAX_DISTANCE_MILES:
        return 0.0
    score = (1 - nearest_dist / CHARGER_MAX_DISTANCE_MILES) * 100
    return round(score, 2)


def precompute_charger_scores(blocks, chargers):
    available_chargers = [c for c in chargers if c["has_available"]]
    return {b["geoid"]: get_charger_score(b, available_chargers) for b in blocks}


def get_autonomy_score(vehicle):
    autonomy_miles = vehicle["autonomy"] * 0.621371
    if season == "Cold Season":
        autonomy_miles *= COLD_WEATHER_RANGE_FACTOR
    score = min(autonomy_miles / AUTONOMY_SCORE_CAP_MILES * 100, 100)
    return round(score, 2)


# historical trip score based on trip start counts per block group.
def historical_trip_score(block_counts):
    block_counts["historical_trip_score"] = (
        block_counts["trip_start_count"].rank(pct=True) * 100
    )
    return block_counts


# Compute overall score for vehicle-block pair to then feed
# to the MILP algorithm


def compute_score(vehicle, block, charger_score, drive_time_minutes=None):
    relo_score = get_relo_cost_score(vehicle, block, drive_time_minutes)
    if relo_score is None:
        return None
    demand_score = get_demand_score(block)
    autonomy_score = get_autonomy_score(vehicle)
    historical_score = block["properties"].get("historical_trip_score", 0.0)
    score = (
        demand_score * DEMAND_WEIGHT
        + charger_score * CHARGER_WEIGHT
        + autonomy_score * BATTERY_WEIGHT
        + historical_score * TRIP_RATE_WEIGHT
        - relo_score * RELO_COST_WEIGHT
    )
    return round(score, 2)


# rough demand tiers based on score data
def get_neighborhood_capacity(neighborhood_demand_score):
    if neighborhood_demand_score >= 70:
        return 4  # high demand — allow up to 4 cars
    elif neighborhood_demand_score >= 40:
        return 3  # medium demand
    elif neighborhood_demand_score >= 15:
        return 2  # low-medium demand
    else:
        return 1  # low demand — still allow 1 car


def assign_blocks_to_neighborhoods(blocks, neighborhoods_geojson_path):
    neighborhoods_geojson_path = os.path.join(SCRIPT_DIR, neighborhoods_geojson_path)

    block_points = gpd.GeoDataFrame(
        {"geoid": [b["geoid"] for b in blocks]},
        geometry=[Point(b["lon"], b["lat"]) for b in blocks],
        crs="EPSG:4269",
    )

    neighborhoods = gpd.read_file(neighborhoods_geojson_path)
    if neighborhoods.crs != block_points.crs:
        neighborhoods = neighborhoods.to_crs(block_points.crs)

    joined = gpd.sjoin(
        block_points,
        neighborhoods[["BDNAME", "geometry"]],
        how = "left",
        predicate="within",
    )

    return dict(zip(joined["geoid"], joined["BDNAME"]))


def get_neighborhood_capacity_map(blocks, geoid_to_neighborhood):

    neighborhood_demand = defaultdict(list)
    for b in blocks:
        nbhd = geoid_to_neighborhood.get(b["geoid"])
        if nbhd:
            neighborhood_demand[nbhd].append(get_demand_score(b))

    return {
        nbhd: get_neighborhood_capacity(sum(scores) / len(scores))
        for nbhd, scores in neighborhood_demand.items()
    }
