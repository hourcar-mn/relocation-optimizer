import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core_functions.data_fetcher import get_vehicles_cached, get_charger_status_cached
from core_functions.scoring import get_current_temp_f
import requests
import geopandas as gpd
from shapely.geometry import Polygon
import logging
import json


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/vehicles")
def vehicles():
    all_vehicles = get_vehicles_cached()
    return [
        {
            "name": v["name"],
            "plate": v["plate"],
            "autonomy_miles": round(v["autonomy"] * 0.621371, 1),
            "lat": v["location"]["latitude"],
            "lon": v["location"]["longitude"],
            "tier": (
                "Emergency" if v["autonomy"] * 0.621371 < 20
                else "Needs charger" if v["autonomy"] * 0.621371 < 40
                else "Normal ops"
            )
        }
        for v in all_vehicles
        if v["name"] is not None and v["plate"] is not None
        and int(float(v["plate"].split("-")[0].strip())) < 500
        and "TOTAL" not in v["name"]
    ]

@app.get("/chargers")
def chargers():
    return get_charger_status_cached()

@app.get("/weather")
def weather():
    return {"temp_f": get_current_temp_f()}

@app.get("/assignments")
def get_assignments(sitting_hours: int = 6):
    from optimizer import run_optimizer
    scores, output = run_optimizer(sitting_threshold_hours=sitting_hours)
    return output

log = logging.getLogger(__name__)

def get_home_zone_gdf():
    """
    Fetches the Evie home zone and returns it as a GeoDataFrame.
    """
    url = "https://gbfs-proxy.hourcar.workers.dev/mds/fleets/HOURCAR-USMSP/gbfs/1/en/geofencing_zones.json"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        evie_zone = next(
            (zone for zone in data["data"]["geofencing_zones"]["features"]
             if zone["properties"]["name"] == "Evie"),
            None
        )
        if evie_zone:
            polygons = [
                Polygon(ring) for polygon in evie_zone["geometry"]["coordinates"] for ring in polygon
            ]
            home_zone_gdf = gpd.GeoDataFrame(
                {"name": ["Evie Home Zone"] * len(polygons)},
                geometry=polygons,
                crs="EPSG:4326"
            )
            return home_zone_gdf
        else:
            log.warning("No Evie home zone found")
            return None
    except requests.RequestException as e:
        log.error(f"Error fetching home zone data: {e}")
        return None


@app.get("/service-area")
def service_area():
    gdf = get_home_zone_gdf()
    if gdf is None or gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    return json.loads(gdf.to_json())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)