import requests
import time


def get_charger_status():
    response = requests.get("https://api.eviecarshare.com/status.json")
    response.raise_for_status()
    chargers = []
    for station in response.json():
        chargers.append({
            "name":      station["name"],
            "lat":       float(station["location"]["latitude"]),
            "lon":       float(station["location"]["longitude"]),
            "available": station["summary"]["available"],
            "in_use":    station["summary"]["in_use"],
            "inoperative": station["summary"]["inoperative"],
            "total_plugs": station["summary"]["total_plugs"],
            "has_available": station["summary"]["available"] > 0,
        })
    return chargers


_charger_cache = {}

def get_charger_status_cached(ttl=120):
    if "chargers" in _charger_cache:
        age = time.time() - _charger_cache["chargers"]["timestamp"]
        if age < ttl:
            return _charger_cache["chargers"]["data"]
    
    print("Fetching charger status from Evie...")
    response = requests.get("https://api.eviecarshare.com/status.json")
    response.raise_for_status()
    raw  = response.json()
    data = []
    for station in raw:
        data.append({
            "name":          station["name"],
            "lat":           float(station["location"]["latitude"]),
            "lon":           float(station["location"]["longitude"]),
            "available":     station["summary"]["available"],
            "in_use":        station["summary"]["in_use"],
            "inoperative":   station["summary"]["inoperative"],
            "total_plugs":   station["summary"]["total_plugs"],
            "has_available": station["summary"]["available"] > 0,
        })
    
    _charger_cache["chargers"] = {"data": data, "timestamp": time.time()}
    print(f"Fetched {len(data)} chargers.")
    return data

