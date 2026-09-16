"""Computes the sub scores for each vehicle and block pair that optimizer.py's
MILP maximizes, and the supporting block/neighborhood data those sub scores
depend on.

compute_score blends demand, charger proximity, battery/autonomy (with a
cold weather range penalty), historical trip rate, and relocation cost into
the single number solve_milp optimizes over. build_score_weights lets the
five weights be overridden per request (see api_server.py's w_* params)
while still summing to 1.0. Charger scoring lives in charger_scoring.py and
neighborhood assignment/capacity in neighborhoods.py.
"""

import os
import functools
import logging
import requests
from datetime import datetime
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from core_functions.data_fetcher import get_conditions, SCRIPT_DIR, BLOCK_GROUPS_GEOJSON
from core_functions.config import (
    DEMAND_WEIGHT,
    CHARGER_WEIGHT,
    BATTERY_WEIGHT,
    DEMAND_ANNUAL_WEIGHT,
    RELO_COST_WEIGHT,
    AUTONOMY_SCORE_CAP_MILES,
    AVG_CITY_SPEED_MPH,
    RELOCATION_OVERHEAD_MINUTES,
    ZERO_VEHICLE_DEMAND_FLOOR,
    TWIN_CITIES_LAT,
    TWIN_CITIES_LON,
    MONTHLY_AVG_TEMP_F,
    COLD_WEATHER_RANGE_TABLE,
    haversine,
)

log = logging.getLogger(__name__)


_temp_cache = {"value": None, "fetched_at": None}
TEMP_CACHE_TTL_SECONDS = 3 * 60 * 60  # 3 hours


def get_current_temp_f():
    now = datetime.now()
    if (
        _temp_cache["value"] is not None
        and _temp_cache["fetched_at"] is not None
        and (now - _temp_cache["fetched_at"]).total_seconds() < TEMP_CACHE_TTL_SECONDS
    ):
        return _temp_cache["value"]

    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": TWIN_CITIES_LAT,
                "longitude": TWIN_CITIES_LON,
                "current": "temperature_2m",
                "temperature_unit": "fahrenheit",
            },
            timeout=5,
        )
        resp.raise_for_status()
        temp = resp.json()["current"]["temperature_2m"]
    except Exception:
        temp = MONTHLY_AVG_TEMP_F[now.month]

    _temp_cache["value"] = temp
    _temp_cache["fetched_at"] = now
    return temp


def _match_vehicle_model(vehicle_name):
    name = vehicle_name.lower()
    if "bolt" in name:
        return "Chevy Bolt"
    if "equinox" in name:
        return "Chevy Equinox"
    if "leaf plus" in name:
        return "Nissan Leaf Plus"
    if "leaf" in name:
        return "Nissan Leaf"
    return None


def get_cold_weather_factor(vehicle_name, temp_f):
    model = _match_vehicle_model(vehicle_name)
    if model is None:
        return 1.0
    table = COLD_WEATHER_RANGE_TABLE[model]
    temps = sorted(table.keys())

    if temp_f >= temps[-1]:
        range_at_temp = table[temps[-1]]
    elif temp_f <= temps[0]:
        range_at_temp = table[temps[0]]
    else:
        range_at_temp = None
        for i in range(len(temps) - 1):
            t_lo, t_hi = temps[i], temps[i + 1]
            if t_lo <= temp_f <= t_hi:
                r_lo, r_hi = table[t_lo], table[t_hi]
                frac = (temp_f - t_lo) / (t_hi - t_lo)
                range_at_temp = r_lo + frac * (r_hi - r_lo)
                break

    baseline_range = table[86]
    return range_at_temp / baseline_range


MEMBER_ADDRESSES_CSV_PATH = "data_files/member_addresses.csv"
MEMBER_BLOCKS_GEOJSON_PATH = BLOCK_GROUPS_GEOJSON


def get_demand_score(block, neighborhood_vehicle_count=None):
    # blends baked in demand percentiles with member density for blocks with thin trip history
    props = block["properties"]
    day_type, season, time_window = get_conditions()
    full_key = f"Full demand: {day_type}, {season}, {time_window} (pct)"
    full_demand = props.get(full_key, 0.0)
    historical = props.get("historical_trip_score", 0.0)
    member_density = get_member_density_pct(block)

    # if block is in bottom 20% of historical trips,
    # weight member density more heavily to avoid starving new blocks

    if historical < 20:
        combined = full_demand * 0.7 + member_density * 0.3
    else:
        combined = full_demand * 0.9 + member_density * 0.1

    if neighborhood_vehicle_count == 0 and combined < ZERO_VEHICLE_DEMAND_FLOOR:
        combined = ZERO_VEHICLE_DEMAND_FLOOR

    return round(combined, 2)


def get_demand_annual_score(block):
    # full demand as an hourly annual average, independent of the current
    # day/season/time window -- distinct from get_demand_score's time-conditional blend
    props = block["properties"]
    return props.get("Full demand: Summarized (pct)", 0.0)


def get_relo_cost_score(vehicle, block, drive_time_minutes=None):
    # penalty for how long it takes to relocate this vehicle to this block
    autonomy_miles = vehicle["autonomy"] * 0.621371
    v_lat = vehicle["location"]["latitude"]
    v_lon = vehicle["location"]["longitude"]
    distance_miles = haversine(v_lat, v_lon, block["lat"], block["lon"])
    if autonomy_miles < distance_miles:
        return None

    if drive_time_minutes is None:
        drive_time_minutes = (distance_miles / AVG_CITY_SPEED_MPH) * 60

    total_labor_minutes = drive_time_minutes + RELOCATION_OVERHEAD_MINUTES
    return round(min(total_labor_minutes / 60 * 100, 100), 2)


@functools.lru_cache(maxsize=1)
def _load_member_density_cached(csv_path, blocks_geojson_path):
    # loads member addresses once per process, joins to block groups, returns {block_id: 0-100 score}
    csv_path = os.path.join(SCRIPT_DIR, csv_path)
    blocks_geojson_path = os.path.join(SCRIPT_DIR, blocks_geojson_path)

    try:
        df = pd.read_csv(csv_path)
    except FileNotFoundError:
        print(f"[member_density] CSV not found at {csv_path}, skipping.")
        return {}
    except Exception as e:
        print(f"[member_density] Failed to read CSV: {e}, skipping.")
        return {}

    if df.empty or "lat" not in df.columns or "lon" not in df.columns:
        print("[member_density] CSV missing lat/lon columns or empty, skipping.")
        return {}

    df = df.dropna(subset=["lat", "lon"])
    # drop sentinel/bad geocodes and anything outside the metro
    df = df[
        (df["lat"] > 40) & (df["lat"] < 50) & (df["lon"] > -100) & (df["lon"] < -85)
    ]
    if df.empty:
        return {}

    try:
        points = gpd.GeoDataFrame(
            df,
            geometry=[Point(lon, lat) for lat, lon in zip(df["lat"], df["lon"])],
            crs="EPSG:4269",
        )

        blocks_gdf = gpd.read_file(blocks_geojson_path)
        if blocks_gdf.crs != points.crs:
            blocks_gdf = blocks_gdf.to_crs(points.crs)

        block_id_col = next(
            (c for c in blocks_gdf.columns if c.lower() == "block_id"), None
        )
        if block_id_col is None:
            print("[member_density] blocks geojson missing block_id column, skipping.")
            return {}
        blocks_gdf = blocks_gdf.rename(columns={block_id_col: "block_id"})

        joined = gpd.sjoin(
            points, blocks_gdf[["block_id", "geometry"]], how="left", predicate="within"
        )
        counts = joined.groupby("block_id").size()
    except Exception as e:
        print(f"[member_density] Spatial join failed: {e}, skipping.")
        return {}

    if counts.empty or counts.max() == 0:
        return {}

    # cap outliers before scaling so one dense block doesn't flatten the rest
    cap = counts.quantile(0.95)
    if cap > 0:
        counts = counts.clip(upper=cap)

    min_c, max_c = counts.min(), counts.max()
    if max_c > min_c:
        normalized = ((counts - min_c) / (max_c - min_c)) * 100
    else:
        normalized = counts.apply(lambda c: 100.0 if c > 0 else 0.0)

    return normalized.round(2).to_dict()


def get_member_density_pct(block):
    density_map = _load_member_density_cached(
        MEMBER_ADDRESSES_CSV_PATH, MEMBER_BLOCKS_GEOJSON_PATH
    )
    return density_map.get(block["block_id"], 0.0)


def get_autonomy_score(vehicle):
    autonomy_miles = vehicle["autonomy"] * 0.621371
    temp_f = get_current_temp_f()
    factor = get_cold_weather_factor(vehicle["name"], temp_f)
    autonomy_miles *= factor

    # score relative to this vehicle's own full-charge range, not one flat
    # mileage cap for every model -- otherwise a full Nissan Leaf (150mi max
    # range) can never score above 60, while a full Chevy Equinox (319mi) hits
    # the 100 cap at ~78% charge. Falls back to the flat cap for models not in
    # the range table.
    model = _match_vehicle_model(vehicle["name"])
    max_range = (
        COLD_WEATHER_RANGE_TABLE[model][86]
        if model in COLD_WEATHER_RANGE_TABLE
        else AUTONOMY_SCORE_CAP_MILES
    )
    return round(min(autonomy_miles / max_range * 100, 100), 2)


def historical_trip_score(block_counts):
    block_counts["historical_trip_score"] = (
        block_counts["trip_start_count"].rank(pct=True) * 100
    )
    return block_counts


def build_score_weights(
    demand=None, charger=None, battery=None, annual_demand=None, relo_cost=None
):
    # falls back to the config defaults for any weight the caller didn't override.
    # The four positive weights are renormalized together so they sum to 1.0 --
    # a true 0-100 ceiling on the additive part of compute_score. relo_cost is
    # applied as a separate subtraction and is intentionally NOT part of that
    # renormalization: folding it into the same budget would mean raising it
    # both shrinks the positive weights AND scales the penalty, double-counting
    # the same dial.
    positive_raw = {
        "demand": DEMAND_WEIGHT if demand is None else demand,
        "charger": CHARGER_WEIGHT if charger is None else charger,
        "battery": BATTERY_WEIGHT if battery is None else battery,
        "annual_demand": (
            DEMAND_ANNUAL_WEIGHT if annual_demand is None else annual_demand
        ),
    }
    positive_total = sum(positive_raw.values())
    if positive_total <= 0:
        positive_raw = {
            "demand": DEMAND_WEIGHT,
            "charger": CHARGER_WEIGHT,
            "battery": BATTERY_WEIGHT,
            "annual_demand": DEMAND_ANNUAL_WEIGHT,
        }
        positive_total = sum(positive_raw.values())
    weights = {k: v / positive_total for k, v in positive_raw.items()}
    weights["relo_cost"] = max(0.0, RELO_COST_WEIGHT if relo_cost is None else relo_cost)
    return weights


def compute_score(vehicle, block, charger_score, drive_time_minutes=None, weights=None, neighborhood_vehicle_count=None):
    if weights is None:
        weights = build_score_weights()
    relo_score = get_relo_cost_score(vehicle, block, drive_time_minutes)
    if relo_score is None:
        return None
    demand_score = get_demand_score(block, neighborhood_vehicle_count)
    autonomy_score = get_autonomy_score(vehicle)
    demand_annual_score = get_demand_annual_score(block)
    score = (
        demand_score * weights["demand"]
        + charger_score * weights["charger"]
        + autonomy_score * weights["battery"]
        + demand_annual_score * weights["annual_demand"]
        - relo_score * weights["relo_cost"]
    )
    return round(score, 2)


