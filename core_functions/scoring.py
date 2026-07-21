from core_functions.data_fetcher import *
from apis.vulog_api import *
from apis.charger_api import *
from apis.open_street_map import get_accurate_route
from core_functions.config import *
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.geometry import Point
from collections import defaultdict
import functools
import pickle
import hashlib

day_type, season, time_window = get_conditions()

# charger drive-time thresholds
CHARGER_NEARBY_DRIVE_MINUTES = 2
CHARGER_SCORE_DECAY_MINUTES = 10
CHARGER_HAVERSINE_PREFILTER_CANDIDATES = 3

_charger_route_cache = {}
_CHARGER_SCORE_CACHE_PATH = os.path.join(
    SCRIPT_DIR, "data_files", "_charger_score_cache.pkl"
)

MEMBER_ADDRESSES_CSV_PATH = "data_files/member_addresses.csv"
MEMBER_BLOCKS_GEOJSON_PATH = "data_files/block_groups_with_demand.geojson"


def get_demand_score(block):
    # blends baked in demand percentiles with member density for blocks with thin trip history
    props = block["properties"]
    full_key = f"Full demand: {day_type}, {season}, {time_window} (pct)"
    lost_key = f"Lost demand: {day_type}, {season}, {time_window} (pct)"
    full_demand = props.get(full_key, 0.0)
    lost_demand = props.get(lost_key, 0.0)
    historical = props.get("historical_trip_score", 0.0)
    member_density = get_member_density_pct(block)

    base = (full_demand * 0.6) + (lost_demand * 0.4)
    if historical < 20:
        combined = base * 0.7 + member_density * 0.3
    else:
        combined = base * 0.9 + member_density * 0.1
    return round(combined, 2)


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


def get_charger_score(block, available_chargers):
    b_lat, b_lon = block["lat"], block["lon"]
    if not available_chargers:
        return 0.0, None

    # only route-check the closest few by haversine, not every charger
    candidates = sorted(
        available_chargers,
        key=lambda c: haversine(b_lat, b_lon, c["lat"], c["lon"]),
    )[:CHARGER_HAVERSINE_PREFILTER_CANDIDATES]

    best_drive_time = None
    for c in candidates:
        drive_time = _get_charger_drive_time(b_lat, b_lon, c["lat"], c["lon"])
        if drive_time is not None and (
            best_drive_time is None or drive_time < best_drive_time
        ):
            best_drive_time = drive_time

    if best_drive_time is None:
        # routing failed for all candidates, fall back to haversine estimate
        nearest_dist = haversine(
            b_lat, b_lon, candidates[0]["lat"], candidates[0]["lon"]
        )
        best_drive_time = (nearest_dist / AVG_CITY_SPEED_MPH) * 60

    score = max(0.0, (1 - best_drive_time / CHARGER_SCORE_DECAY_MINUTES) * 100)
    return round(score, 2), round(best_drive_time, 2)


def _charger_signature(chargers):
    # location-only fingerprint, ignores live plug counts so cache doesn't churn
    sig = sorted((round(c["lat"], 4), round(c["lon"], 4)) for c in chargers)
    return hashlib.md5(str(sig).encode()).hexdigest()


def precompute_charger_scores(blocks, chargers):
    block_geoids = tuple(sorted(b["geoid"] for b in blocks))
    signature = (block_geoids, _charger_signature(chargers))

    if os.path.exists(_CHARGER_SCORE_CACHE_PATH):
        try:
            with open(_CHARGER_SCORE_CACHE_PATH, "rb") as f:
                cached_sig, cached_scores, cached_times = pickle.load(f)
            if cached_sig == signature:
                print("Using cached charger scores.")
                return cached_scores, cached_times
        except Exception:
            pass

    available_chargers = [c for c in chargers if c["has_available"]]
    scores, drive_times = {}, {}
    print(f"Computing charger scores for {len(blocks)} blocks...")
    for i, b in enumerate(blocks):
        score, drive_time = get_charger_score(b, available_chargers)
        scores[b["geoid"]] = score
        drive_times[b["geoid"]] = drive_time
        if (i + 1) % 20 == 0 or (i + 1) == len(blocks):
            print(f"  {i + 1}/{len(blocks)}")

    try:
        os.makedirs(os.path.dirname(_CHARGER_SCORE_CACHE_PATH), exist_ok=True)
        with open(_CHARGER_SCORE_CACHE_PATH, "wb") as f:
            pickle.dump((signature, scores, drive_times), f)
        print("Charger scores cached to disk.")
    except Exception as e:
        print(f"Failed to write charger score cache: {e}")

    return scores, drive_times


@functools.lru_cache(maxsize=1)
def _load_member_density_cached(csv_path, blocks_geojson_path):
    # loads member addresses once per process, joins to block groups, returns {geoid: 0-100 score}
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

        # handle GEOID vs geoid casing
        geoid_col = next((c for c in blocks_gdf.columns if c.lower() == "geoid"), None)
        if geoid_col is None:
            print("[member_density] blocks geojson missing GEOID column, skipping.")
            return {}
        blocks_gdf = blocks_gdf.rename(columns={geoid_col: "geoid"})

        joined = gpd.sjoin(
            points, blocks_gdf[["geoid", "geometry"]], how="left", predicate="within"
        )
        counts = joined.groupby("geoid").size()
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
    return density_map.get(block["geoid"], 0.0)


def get_autonomy_score(vehicle):
    autonomy_miles = vehicle["autonomy"] * 0.621371
    if season == "Cold Season":
        autonomy_miles *= COLD_WEATHER_RANGE_FACTOR
    return round(min(autonomy_miles / AUTONOMY_SCORE_CAP_MILES * 100, 100), 2)


def historical_trip_score(block_counts):
    block_counts["historical_trip_score"] = (
        block_counts["trip_start_count"].rank(pct=True) * 100
    )
    return block_counts


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


def get_neighborhood_capacity(demand_score):
    if demand_score >= 70:
        return 4
    elif demand_score >= 40:
        return 3
    elif demand_score >= 15:
        return 2
    return 1


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
        how="left",
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
