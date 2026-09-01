"""Computes the sub scores for each vehicle and block pair that optimizer.py's
MILP maximizes, and the supporting block/neighborhood data those sub scores
depend on.

compute_score blends demand, charger proximity, battery/autonomy (with a
cold weather range penalty), historical trip rate, and relocation cost into
the single number solve_milp optimizes over. build_score_weights lets the
five weights be overridden per request (see api_server.py's w_* params)
while still summing to 1.0. The charger score and neighborhood assignment
caches here are keyed on a signature of their inputs so they rebuild
automatically when blocks or chargers change, rather than on a fixed TTL.
"""

import os
import functools
import pickle
import hashlib
import concurrent
import logging
import requests
import time
from datetime import datetime
from collections import defaultdict
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from core_functions.data_fetcher import get_conditions, SCRIPT_DIR, BLOCK_GROUPS_GEOJSON
from apis.open_street_map import get_accurate_route
from core_functions.config import (
    DEMAND_WEIGHT,
    CHARGER_WEIGHT,
    BATTERY_WEIGHT,
    DEMAND_ANNUAL_WEIGHT,
    RELO_COST_WEIGHT,
    AUTONOMY_SCORE_CAP_MILES,
    AVG_CITY_SPEED_MPH,
    RELOCATION_OVERHEAD_MINUTES,
    CHARGER_SCORE_DECAY_MINUTES,
    CHARGER_HAVERSINE_PREFILTER_CANDIDATES,
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


_charger_route_cache = {}
_CHARGER_SCORE_CACHE_PATH = os.path.join(
    SCRIPT_DIR, "data_files", "_charger_score_cache.pkl"
)

MEMBER_ADDRESSES_CSV_PATH = "data_files/member_addresses.csv"
MEMBER_BLOCKS_GEOJSON_PATH = BLOCK_GROUPS_GEOJSON


def get_demand_score(block):
    # blends baked in demand percentiles with member density for blocks with thin trip history
    props = block["properties"]
    day_type, season, time_window = get_conditions()
    full_key = f"Full demand: {day_type}, {season}, {time_window} (pct)"
    lost_key = f"Lost demand: {day_type}, {season}, {time_window} (pct)"
    full_demand = props.get(full_key, 0.0)
    # lost_demand = props.get(lost_key, 0.0)
    historical = props.get("historical_trip_score", 0.0)
    member_density = get_member_density_pct(block)

    # if block is in bottom 20% of historical trips,
    # weight member density more heavily to avoid starving new blocks

    if historical < 20:
        combined = full_demand * 0.7 + member_density * 0.3
    else:
        combined = full_demand * 0.9 + member_density * 0.1
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


def _get_charger_drive_time(b_lat, b_lon, c_lat, c_lon):
    # per-pair cache to avoid duplicate routing calls within one run
    key = (round(b_lat, 5), round(b_lon, 5), round(c_lat, 5), round(c_lon, 5))
    if key in _charger_route_cache:
        return _charger_route_cache[key]

    drive_time = None
    try:
        route = get_accurate_route(b_lat, b_lon, c_lat, c_lon)
        if route and route.get("time_minutes") is not None:
            drive_time = route["time_minutes"]
    except Exception:
        drive_time = None

    _charger_route_cache[key] = drive_time
    return drive_time


def get_charger_score(block, nearest_chargers):
    b_lat = block["lat"]
    b_lon = block["lon"]
    if not nearest_chargers:
        return 0.0, None

    best_drive_time = None
    for c in nearest_chargers:
        drive_time = _get_charger_drive_time(b_lat, b_lon, c["lat"], c["lon"])
        if drive_time is not None and (
            best_drive_time is None or drive_time < best_drive_time
        ):
            best_drive_time = drive_time

    if best_drive_time is None:
        nearest_dist = haversine(
            b_lat, b_lon, nearest_chargers[0]["lat"], nearest_chargers[0]["lon"]
        )
        best_drive_time = (nearest_dist / AVG_CITY_SPEED_MPH) * 60

    score = max(0.0, (1 - best_drive_time / CHARGER_SCORE_DECAY_MINUTES) * 100)
    return round(score, 2), round(best_drive_time, 2)


def _charger_signature(chargers):
    # location-only fingerprint, ignores live plug counts so cache doesn't churn
    sig = sorted((round(c["lat"], 4), round(c["lon"], 4)) for c in chargers)
    return hashlib.md5(str(sig).encode()).hexdigest()


CHARGER_SCORE_CACHE_TTL = 600  # seconds, match get_charger_status_cached's typical ttl


def precompute_charger_scores(blocks, chargers):
    block_ids = tuple(sorted(b["block_id"] for b in blocks))
    signature = (block_ids, _charger_signature(chargers))
    if os.path.exists(_CHARGER_SCORE_CACHE_PATH):
        try:
            age = time.time() - os.path.getmtime(_CHARGER_SCORE_CACHE_PATH)
            if age < CHARGER_SCORE_CACHE_TTL:
                with open(_CHARGER_SCORE_CACHE_PATH, "rb") as f:
                    cached_sig, cached_scores, cached_times = pickle.load(f)
                if cached_sig == signature:
                    print("Using cached charger scores.")
                    return cached_scores, cached_times
        except Exception:
            pass

    nearest_by_block = _precompute_nearest_chargers(blocks, chargers)

    def _score_one(b):
        return b["block_id"], get_charger_score(b, nearest_by_block[b["block_id"]])

    scores = {}
    times = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        for block_id, (score, drive_time) in executor.map(_score_one, blocks):
            scores[block_id] = score
            times[block_id] = drive_time

    try:
        os.makedirs(os.path.dirname(_CHARGER_SCORE_CACHE_PATH), exist_ok=True)
        with open(_CHARGER_SCORE_CACHE_PATH, "wb") as f:
            pickle.dump((signature, scores, times), f)
    except Exception as e:
        log.error(f"Failed to write charger score cache: {e}")

    return scores, times


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
    return round(min(autonomy_miles / AUTONOMY_SCORE_CAP_MILES * 100, 100), 2)


def historical_trip_score(block_counts):
    block_counts["historical_trip_score"] = (
        block_counts["trip_start_count"].rank(pct=True) * 100
    )
    return block_counts


def build_score_weights(
    demand=None, charger=None, battery=None, annual_demand=None, relo_cost=None
):
    # falls back to the config defaults for any weight the caller didn't override,
    # then renormalizes all five so they sum to 1.0. relo_cost is applied as a
    # subtraction in compute_score, but its magnitude still draws from the same
    # 1.0 budget as the positive weights.
    raw = {
        "demand": DEMAND_WEIGHT if demand is None else demand,
        "charger": CHARGER_WEIGHT if charger is None else charger,
        "battery": BATTERY_WEIGHT if battery is None else battery,
        "annual_demand": (
            DEMAND_ANNUAL_WEIGHT if annual_demand is None else annual_demand
        ),
        "relo_cost": RELO_COST_WEIGHT if relo_cost is None else relo_cost,
    }
    total = sum(raw.values())
    if total <= 0:
        raw = {
            "demand": DEMAND_WEIGHT,
            "charger": CHARGER_WEIGHT,
            "battery": BATTERY_WEIGHT,
            "annual_demand": DEMAND_ANNUAL_WEIGHT,
            "relo_cost": RELO_COST_WEIGHT,
        }
        total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def compute_score(vehicle, block, charger_score, drive_time_minutes=None, weights=None):
    if weights is None:
        weights = build_score_weights()
    relo_score = get_relo_cost_score(vehicle, block, drive_time_minutes)
    if relo_score is None:
        return None
    demand_score = get_demand_score(block)
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


def get_neighborhood_capacity(demand_score):
    if demand_score >= 70:
        return 4
    elif demand_score >= 40:
        return 3
    elif demand_score >= 15:
        return 2
    return 1


_NEIGHBORHOOD_ASSIGNMENT_CACHE_PATH = os.path.join(
    SCRIPT_DIR, "data_files", "_neighborhood_assignment_cache.pkl"
)


def assign_points_to_neighborhoods(points, neighborhoods_geojson_path, id_key="block_id"):
    """Spatial-joins each point's own lat/lon against the neighborhood polygons.

    Used for both block groups (id_key="block_id") and pods (id_key="pod_id") —
    a block group's centroid and a pod sitting in that block group can fall in
    different neighborhood polygons, so callers that need the pod's real
    neighborhood must pass pods here rather than reusing the block assignment.
    """
    ids = tuple(sorted(p[id_key] for p in points))
    cache_path = (
        _NEIGHBORHOOD_ASSIGNMENT_CACHE_PATH
        if id_key == "block_id"
        else _NEIGHBORHOOD_ASSIGNMENT_CACHE_PATH.replace(".pkl", f"_{id_key}.pkl")
    )
    signature = (ids, neighborhoods_geojson_path)

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                cached_sig, cached_result = pickle.load(f)
            if cached_sig == signature:
                print(f"Using cached {id_key}-to-neighborhood assignments.")
                return cached_result
        except Exception:
            pass

    neighborhoods_geojson_path_full = os.path.join(
        SCRIPT_DIR, neighborhoods_geojson_path
    )

    point_gdf = gpd.GeoDataFrame(
        {id_key: [p[id_key] for p in points]},
        geometry=[Point(p["lon"], p["lat"]) for p in points],
        crs="EPSG:4269",
    )

    neighborhoods = gpd.read_file(neighborhoods_geojson_path_full)
    if neighborhoods.crs != point_gdf.crs:
        neighborhoods = neighborhoods.to_crs(point_gdf.crs)

    # "within" misses points that land exactly on a neighborhood boundary
    # (e.g. a pod snapped to a street that traces the edge) — shapely doesn't
    # count the boundary as "within" the polygon, so those points came back
    # with no neighborhood at all. "intersects" catches boundary points too.
    joined = gpd.sjoin(
        point_gdf,
        neighborhoods[["BDNAME", "geometry"]],
        how="left",
        predicate="intersects",
    ).drop_duplicates(subset=id_key)
    result = {
        pid: (name if pd.notna(name) else None)
        for pid, name in zip(joined[id_key], joined["BDNAME"])
    }

    # anything still unmatched is genuinely outside every polygon (gaps in
    # coverage, or a point just past the service area edge) — snap it to
    # whichever neighborhood is physically closest instead of leaving it blank.
    missing_ids = [pid for pid, name in result.items() if name is None]
    if missing_ids:
        missing_gdf = point_gdf[point_gdf[id_key].isin(missing_ids)]
        nearest = gpd.sjoin_nearest(missing_gdf, neighborhoods[["BDNAME", "geometry"]])
        for pid, name in zip(nearest[id_key], nearest["BDNAME"]):
            result[pid] = name

    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump((signature, result), f)
    except Exception as e:
        log.error(f"Failed to write neighborhood assignment cache: {e}")

    return result


def assign_blocks_to_neighborhoods(blocks, neighborhoods_geojson_path):
    return assign_points_to_neighborhoods(blocks, neighborhoods_geojson_path, id_key="block_id")


def get_neighborhood_capacity_map(blocks, block_id_to_neighborhood):
    neighborhood_demand = defaultdict(list)
    for b in blocks:
        nbhd = block_id_to_neighborhood.get(b["block_id"])
        if nbhd:
            neighborhood_demand[nbhd].append(get_demand_score(b))

    return {
        nbhd: get_neighborhood_capacity(sum(scores) / len(scores))
        for nbhd, scores in neighborhood_demand.items()
    }


def _precompute_nearest_chargers(blocks, available_chargers):
    nearest_by_block = {}
    for b in blocks:
        candidates = sorted(
            available_chargers,
            key=lambda c: haversine(b["lat"], b["lon"], c["lat"], c["lon"]),
        )[:CHARGER_HAVERSINE_PREFILTER_CANDIDATES]
        nearest_by_block[b["block_id"]] = candidates
    return nearest_by_block
