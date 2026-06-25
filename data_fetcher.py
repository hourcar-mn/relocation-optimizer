#import pulp 
from vulog_api import *
import json
from pathlib import Path
import json
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt
from shapely.geometry import Point
from datetime import datetime

token    = get_token()
vehicles = get_vehicles(token)
trips = get_trips_history(token)

#Get full list of vehicles. 
def vehicles_data():
    evies = []
    for v in vehicles:
        if v['name'] is not None and v['plate'] is not None:
            plate = v.get('plate', '')
            if plate:
                vehicle_num = int(float(plate.split('-')[0].strip()))
                if vehicle_num < 500:
                    evies.append(v)
    return evies

#Get list of blocks. 
def load_block_groups():
    geojson_path = Path(__file__).parent / "block_groups_with_demand.geojson"
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    blocks = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        blocks.append({
            "name":       props.get("NAMELSAD"),
            "geoid":      props.get("GEOID"),
            "lat":        float(props.get("INTPTLAT")),
            "lon":        float(props.get("INTPTLON")),
            "chargers":   props.get("Chargers", 0),
            "properties": props    # ← add this line
        })
    return blocks

#Get list of neighborhoods and their coordinates.
def load_neighborhoods():
    geojson_path = Path(__file__).parent / "neighborhoods.geojson"
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    neighborhoods = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        neighborhoods.append({
            "name": props.get("BDNAME"),
            "coordinates": props.get("coordinates")
            # "lon": float(props.get("INTPTLON")),
        })
    return neighborhoods

# Get list of block group names.
def block_group_names():
    blocks = load_block_groups()
    return [b['name'] for b in blocks]

# Get list of neighborhood names.
def neighborhood_names():
    neighborhoods = load_neighborhoods()
    return [n['name'] for n in neighborhoods]

#Gets current conditions (day type, season, time window) for demand score calculation.
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

_cache = {}

def get_vehicles_cached(ttl=300):
    if "vehicles" in _cache:
        age = time.time() - _cache["vehicles"]["timestamp"]
        if age < ttl:
            return _cache["vehicles"]["data"]
    
    print("Fetching vehicles from Vulog...")
    token = get_token()
    url   = f"{VULOG_SERVER}/boapi/proxy/fleetmanager/public/fleets/{FLEET_ID}/vehicles"
    response = requests.get(url, headers=get_headers(token))
    response.raise_for_status()
    data = response.json()
    
    _cache["vehicles"] = {"data": data, "timestamp": time.time()}
    print(f"Fetched {len(data)} vehicles.")
    return data

def get_trip_history(ttl=300):
    if "trips" in _cache:
        age = time.time() - _cache["trips"]["timestamp"]
        if age < ttl:
            return _cache["trips"]["data"]
    
    print("Fetching trip history from Vulog...")
    token = get_token()
    url   = f"{VULOG_SERVER}//boapi/proxy/trip/fleets/{{fleet_id}}/trips"
    response = requests.get(url, headers=get_headers(token))
    response.raise_for_status()
    data = response.json()
    
    _cache["trips"] = {"data": data, "timestamp": time.time()}
    print(f"Fetched {len(data)} trips.")
    return data

#categorize trip starts by block group using spatial join

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def categorize_trip_starts_by_block(trips_csv_path, block_groups_geojson_path,
                                     lat_col="StartLat", lon_col="StartLong",
                                     block_id_col="GEOID"):
    # Resolve paths relative to this script's location, regardless of cwd
    trips_csv_path = os.path.join(SCRIPT_DIR, trips_csv_path)
    block_groups_geojson_path = os.path.join(SCRIPT_DIR, block_groups_geojson_path)

    # Load trips
    trips = pd.read_csv(trips_csv_path)

    # Drop missing/invalid coordinates
    trips = trips.dropna(subset=[lat_col, lon_col])
    trips = trips[(trips[lat_col].between(-90, 90)) & (trips[lon_col].between(-180, 180))]

    # Build points GeoDataFrame (CRS matches the block groups geojson, EPSG:4269)
    geometry = [Point(xy) for xy in zip(trips[lon_col], trips[lat_col])]
    trips_gdf = gpd.GeoDataFrame(trips, geometry=geometry, crs="EPSG:4269")

    # Load block groups
    blocks = gpd.read_file(block_groups_geojson_path)
    blocks[block_id_col] = blocks[block_id_col].astype(str)

    # Spatial join: point-in-polygon
    trips_with_block = gpd.sjoin(
        trips_gdf,
        blocks[[block_id_col, "geometry"]],
        how="left",
        predicate="within"
    )

    # Count trips per block
    counts = (
        trips_with_block
        .groupby(block_id_col)
        .size()
        .reset_index(name="trip_start_count")
        .sort_values("trip_start_count", ascending=False)
    )

    block_counts = blocks.merge(counts, on=block_id_col, how="left")
    block_counts["trip_start_count"] = block_counts["trip_start_count"].fillna(0).astype(int)
    block_counts = block_counts.sort_values("trip_start_count", ascending=False)
    return block_counts, trips_with_block

#attach historical trip scores to blocks based on trip counts
def attach_historical_trip_scores(blocks, block_counts, block_id_col="GEOID"):
    score_lookup = block_counts.set_index(block_id_col)["historical_trip_score"].to_dict()

    for block in blocks:
        geoid = block["geoid"]
        block["properties"]["historical_trip_score"] = score_lookup.get(geoid, 0.0)

    return blocks


# if __name__ == "__main__":
#     blocks = load_block_groups()
#     print(f"Total block groups: {len(blocks)}")
#     for b in blocks:
#         print(f"  {b['name']} — chargers: {b['chargers']}")