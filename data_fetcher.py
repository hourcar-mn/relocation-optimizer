#import pulp 
from vulog_api import *
import json
from pathlib import Path
import json
from pathlib import Path
from datetime import datetime
from datetime import datetime

token    = get_token()
vehicles = get_vehicles(token)

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

# if __name__ == "__main__":
#     blocks = load_block_groups()
#     print(f"Total block groups: {len(blocks)}")
#     for b in blocks:
#         print(f"  {b['name']} — chargers: {b['chargers']}")