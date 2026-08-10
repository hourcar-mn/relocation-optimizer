import pickle as pkl
import os
import json
import time
import requests
import pandas as pd
import geopandas as gpd
from pathlib import Path
from shapely.geometry import Point
from datetime import datetime
from apis.vulog_api import get_token, get_headers, VULOG_SERVER, FLEET_ID
from shapely.geometry import shape
from core_functions.config import haversine  

CACHE_DIR = Path(__file__).parent.parent / "data_files" / "_api_cache"
CACHE_DIR.mkdir(exist_ok=True)


def _read_disk_cache(name, ttl):
    cache_path = CACHE_DIR / f"{name}.json"
    if not cache_path.exists():
        return None
    age = time.time() - cache_path.stat().st_mtime
    if age >= ttl:
        return None
    with open(cache_path, "r") as f:
        return json.load(f)


def _write_disk_cache(name, data):
    cache_path = CACHE_DIR / f"{name}.json"
    with open(cache_path, "w") as f:
        json.dump(data, f)


def get_vehicles_cached(ttl=600):
    cached = _read_disk_cache("vehicles", ttl)
    if cached is not None:
        print("Using cached vehicles.")
        return cached

    print("Fetching vehicles from Vulog...")
    token = get_token()
    url = f"{VULOG_SERVER}/boapi/proxy/fleetmanager/public/fleets/{FLEET_ID}/vehicles"

    response = requests.get(url, headers=get_headers(token))
    response.raise_for_status()
    data = response.json()

    total_pages = int(response.headers.get("totalpages", 1))
    if total_pages > 1:
        print(
            f"Vehicles response is paginated ({total_pages} pages) — fetching remaining pages..."
        )
        for page in range(1, total_pages):
            page_response = requests.get(
                url, headers=get_headers(token), params={"page": page}
            )
            page_response.raise_for_status()
            data.extend(page_response.json())

    _write_disk_cache("vehicles", data)
    print(f"Fetched {len(data)} vehicles.")
    return data


def get_charger_status_cached(ttl=600):
    cached = _read_disk_cache("chargers", ttl)
    if cached is not None:
        print("Using cached chargers.")
        return cached

    print("Fetching charger status from Evie...")
    response = requests.get("https://api.eviecarshare.com/status.json")
    response.raise_for_status()
    raw = response.json()
    data = []
    for station in raw:
        data.append(
            {
                "name": station["name"],
                "lat": float(station["location"]["latitude"]),
                "lon": float(station["location"]["longitude"]),
                "available": station["summary"]["available"],
                "in_use": station["summary"]["in_use"],
                "inoperative": station["summary"]["inoperative"],
                "total_plugs": station["summary"]["total_plugs"],
                "has_available": station["summary"]["available"] > 0,
            }
        )

    _write_disk_cache("chargers", data)
    print(f"Fetched {len(data)} chargers.")
    return data


# Get list of blocks.
def load_block_groups():
    geojson_path = (
        Path(__file__).parent.parent / "data_files" / "block_groups_with_demand.geojson"
    )
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blocks = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        blocks.append(
            {
                "name": props.get("NAMELSAD"),
                "geoid": props.get("GEOID"),
                "lat": float(props.get("INTPTLAT")),
                "lon": float(props.get("INTPTLON")),
                "chargers": props.get("Chargers", 0),
                "properties": props,
                "geometry": feature.get("geometry"),   # <-- add this line
            }
        )
    return blocks


# Get list of neighborhoods and their coordinates.
def load_neighborhoods():
    geojson_path = Path(__file__).parent / "neighborhoods.geojson"
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    neighborhoods = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        neighborhoods.append(
            {
                "name": props.get("BDNAME"),
                "coordinates": props.get("coordinates"),
            }
        )
    return neighborhoods


# Get list of block group names.
def block_group_names():
    blocks = load_block_groups()
    return [b["name"] for b in blocks]


# Get list of neighborhood names.
def neighborhood_names():
    neighborhoods = load_neighborhoods()
    return [n["name"] for n in neighborhoods]


# Gets current conditions (day type, season, time window) for demand score calculation.
def get_conditions():
    now = datetime.now()
    # Day type
    day_type = "Weekday" if now.weekday() < 5 else "Weekend"
    # Season
    month = now.month
    season = "Cold Season" if month in [11, 12, 1, 2, 3] else "Warm Season"
    # Time window
    hour = now.hour
    if 0 <= hour < 6:
        time_window = "12am-6am"
    elif 6 <= hour < 10:
        time_window = "6am-10am"
    elif 10 <= hour < 15:
        time_window = "10am-3pm"
    elif 15 <= hour < 19:
        time_window = "3pm-7pm"
    else:
        time_window = "7pm-12am"
    return day_type, season, time_window


_block_cache = None


def load_block_groups_cached():
    global _block_cache
    if _block_cache is not None:
        return _block_cache

    print("Loading block groups from geojson...")
    _block_cache = load_block_groups()
    print(f"Loaded {len(_block_cache)} block groups.")
    return _block_cache


# categorize trip starts by block group using spatial join

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def categorize_trip_starts_by_block(
    trips_csv_path,
    block_groups_geojson_path,
    lat_col="StartLat",
    lon_col="StartLong",
    block_id_col="GEOID",
):
    # Resolve paths relative to this script's location, regardless of cwd
    trips_csv_path = os.path.join(SCRIPT_DIR, trips_csv_path)
    block_groups_geojson_path = os.path.join(SCRIPT_DIR, block_groups_geojson_path)

    # Load trips
    trips = pd.read_csv(trips_csv_path)

    # Drop missing/invalid coordinates
    trips = trips.dropna(subset=[lat_col, lon_col])
    trips = trips[
        (trips[lat_col].between(-90, 90)) & (trips[lon_col].between(-180, 180))
    ]

    # Build points GeoDataFrame (CRS matches the block groups geojson, EPSG:4269)
    geometry = [Point(xy) for xy in zip(trips[lon_col], trips[lat_col])]
    trips_gdf = gpd.GeoDataFrame(trips, geometry=geometry, crs="EPSG:4269")

    # Load block groups
    blocks = gpd.read_file(block_groups_geojson_path)
    blocks[block_id_col] = blocks[block_id_col].astype(str)

    # Spatial join: point-in-polygon
    trips_with_block = gpd.sjoin(
        trips_gdf, blocks[[block_id_col, "geometry"]], how="left", predicate="within"
    )

    # Count trips per block
    counts = (
        trips_with_block.groupby(block_id_col)
        .size()
        .reset_index(name="trip_start_count")
        .sort_values("trip_start_count", ascending=False)
    )

    block_counts = blocks.merge(counts, on=block_id_col, how="left")
    block_counts["trip_start_count"] = (
        block_counts["trip_start_count"].fillna(0).astype(int)
    )
    block_counts = block_counts.sort_values("trip_start_count", ascending=False)
    return block_counts, trips_with_block


# attach historical trip scores to blocks based on trip counts
def attach_historical_trip_scores(blocks, block_counts, block_id_col="GEOID"):
    score_lookup = block_counts.set_index(block_id_col)[
        "historical_trip_score"
    ].to_dict()

    for block in blocks:
        geoid = block["geoid"]
        block["properties"]["historical_trip_score"] = score_lookup.get(geoid, 0.0)

    return blocks


def attach_demand_percentiles(blocks):

    df = pd.DataFrame([b["properties"] for b in blocks])

    demand_cols = [
        c
        for c in df.columns
        if c.startswith("Full demand:") or c.startswith("Lost demand:")
    ]
    for col in demand_cols:
        df[f"{col} (pct)"] = df[col].rank(pct=True) * 100

    for i, block in enumerate(blocks):
        for col in demand_cols:
            block["properties"][f"{col} (pct)"] = df.iloc[i][f"{col} (pct)"]

    return blocks


def get_vehicle_current_blocks(evies, blocks):
    vehicle_points = gpd.GeoDataFrame(
        {"name": [v["name"] for v in evies]},
        geometry=[
            Point(v["location"]["longitude"], v["location"]["latitude"]) for v in evies
        ],
        crs="EPSG:4269",
    )

    blocks_gdf = gpd.GeoDataFrame(
        {"geoid": [b["geoid"] for b in blocks]},
        geometry=[shape(b["geometry"]) for b in blocks],
        crs="EPSG:4269",
    )

    joined = gpd.sjoin(vehicle_points, blocks_gdf, how="left", predicate="within")
    result = dict(zip(joined["name"], joined["geoid"]))

    # fallback: if a vehicle didn't fall inside any block (edge cases,
    # sliver gaps between polygons), assign it to the nearest block instead
    # of leaving it unmatched
    missing = [name for name, geoid in result.items() if pd.isna(geoid)]
    if missing:
        for v in evies:
            if v["name"] in missing:
                v_lat = v["location"]["latitude"]
                v_lon = v["location"]["longitude"]
                nearest = min(
                    blocks, key=lambda b: haversine(v_lat, v_lon, b["lat"], b["lon"])
                )
                result[v["name"]] = nearest["geoid"]

    return result


TRIP_CACHE = CACHE_DIR / "trip_block_counts.pkl"
TRIP_CACHE_TTL = 3600  # 1 hour


def categorize_trip_starts_by_block_cached(
    trips_csv_path,
    block_groups_geojson_path,
    lat_col="StartLat",
    lon_col="StartLong",
    block_id_col="GEOID",
):
    if TRIP_CACHE.exists():
        age = time.time() - TRIP_CACHE.stat().st_mtime
        if age < TRIP_CACHE_TTL:
            print("Using cached trip-block categorization.")
            with open(TRIP_CACHE, "rb") as f:
                return pkl.load(f)

    print("Categorizing trip starts by block group...")
    block_counts, trips_with_block = categorize_trip_starts_by_block(
        trips_csv_path,
        block_groups_geojson_path,
        lat_col=lat_col,
        lon_col=lon_col,
        block_id_col=block_id_col,
    )

    with open(TRIP_CACHE, "wb") as f:
        pkl.dump((block_counts, trips_with_block), f)

    return block_counts, trips_with_block

