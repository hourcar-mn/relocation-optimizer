import requests
import time


def get_charger_status():
    response = requests.get("https://api.eviecarshare.com/status.json")
    response.raise_for_status()
    chargers = []
    for station in response.json():
        chargers.append(
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
    return chargers






