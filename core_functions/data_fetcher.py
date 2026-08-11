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


# Primary block groups file: demand percentiles + trip totals, both broken out
# by day type / season / 6-hour time window.
BLOCK_GROUPS_GEOJSON = "data_files/block_groups_with_demand (8_10).geojson"


# Get list of blocks.
def load_block_groups():
    geojson_path = Path(__file__).parent.parent / BLOCK_GROUPS_GEOJSON
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blocks = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        blocks.append(
            {
                "name": props.get("NAMELSAD"),
                "block_id": props.get("block_id"),
                "lat": float(props.get("INTPTLAT")),
                "lon": float(props.get("INTPTLON")),
                "chargers": props.get("Chargers", 1 if props.get("HasCharger") else 0),
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
    # Time window (four 6-hour windows, matching the demand geojson's columns)
    hour = now.hour
    if 0 <= hour < 6:
        time_window = "12am-6am"
    elif 6 <= hour < 12:
        time_window = "6am-12pm"
    elif 12 <= hour < 18:
        time_window = "12pm-6pm"
    else:
        time_window = "6pm-12am"
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


SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Trip totals per block group, read straight from the demand geojson's
# "Total trips: Summarized" column instead of re-deriving them from a spatial
# join against the raw trip history CSV.
def trip_counts_from_blocks(blocks, trip_count_key="Total trips: Summarized"):
    block_counts = pd.DataFrame(
        {
            "block_id": [b["block_id"] for b in blocks],
            "trip_start_count": [
                b["properties"].get(trip_count_key) or 0 for b in blocks
            ],
        }
    )
    return block_counts


# attach historical trip scores to blocks based on trip counts
def attach_historical_trip_scores(blocks, block_counts, block_id_col="block_id"):
    score_lookup = block_counts.set_index(block_id_col)[
        "historical_trip_score"
    ].to_dict()

    for block in blocks:
        block_id = block["block_id"]
        block["properties"]["historical_trip_score"] = score_lookup.get(block_id, 0.0)

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
        {"block_id": [b["block_id"] for b in blocks]},
        geometry=[shape(b["geometry"]) for b in blocks],
        crs="EPSG:4269",
    )

    joined = gpd.sjoin(vehicle_points, blocks_gdf, how="left", predicate="within")
    result = dict(zip(joined["name"], joined["block_id"]))

    # fallback: if a vehicle didn't fall inside any block (edge cases,
    # sliver gaps between polygons), assign it to the nearest block instead
    # of leaving it unmatched
    missing = [name for name, block_id in result.items() if pd.isna(block_id)]
    if missing:
        for v in evies:
            if v["name"] in missing:
                v_lat = v["location"]["latitude"]
                v_lon = v["location"]["longitude"]
                nearest = min(
                    blocks, key=lambda b: haversine(v_lat, v_lon, b["lat"], b["lon"])
                )
                result[v["name"]] = nearest["block_id"]

    return result



