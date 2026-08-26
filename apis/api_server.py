"""FastAPI server that exposes the optimizer's data and results over HTTP, for
the frontend and the demand-dashboard Worker.

The vehicle, charger, weather, and service area endpoints proxy the cached
fetchers in core_functions/data_fetcher.py so callers don't need Vulog/Evie
credentials of their own. /assignments and /tasks trigger a full optimizer
run (optimizer.py) and are serialized behind _run_lock, since the optimizer
writes shared cache files to disk and a second concurrent run would race the
first one through them. /tasks is for local/direct use only. In production
the Worker serves /api/tasks from the "tasks" field of a cached /assignments
run instead of triggering a second run here that takes several minutes.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core_functions.data_fetcher import get_home_zone_gdf_cached, get_vehicles_cached, get_charger_status_cached
from core_functions.scoring import get_current_temp_f
from core_functions.tasks import assignments_to_tasks
from core_functions.config import (
    DEMAND_WEIGHT,
    CHARGER_WEIGHT,
    BATTERY_WEIGHT,
    DEMAND_ANNUAL_WEIGHT,
    RELO_COST_WEIGHT,
)
import json
import threading
from datetime import datetime, timezone



app = FastAPI()

# The optimizer writes shared cache files on disk, so only one run at a time.
# A second request (cron firing while someone hits "Run optimizer") waits here
# instead of racing the first one through the caches.
_run_lock = threading.Lock()


@app.get("/health")
def health():
    return {"ok": True}

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

@app.get("/weights/defaults")
def weight_defaults():
    return {
        "demand": DEMAND_WEIGHT,
        "charger": CHARGER_WEIGHT,
        "battery": BATTERY_WEIGHT,
        "annual_demand": DEMAND_ANNUAL_WEIGHT,
        "relo_cost": RELO_COST_WEIGHT,
    }

@app.get("/assignments")
def get_assignments(
    sitting_hours: int = 6,
    w_demand: Optional[float] = None,
    w_charger: Optional[float] = None,
    w_battery: Optional[float] = None,
    w_trip: Optional[float] = None,
    w_relo: Optional[float] = None,
):
    from optimizer import run_optimizer
    weights = {
        "demand": w_demand,
        "charger": w_charger,
        "battery": w_battery,
        "annual_demand": w_trip,
        "relo_cost": w_relo,
    }
    with _run_lock:
        _, output = run_optimizer(sitting_threshold_hours=sitting_hours, weights=weights)
    generated_at = datetime.now(timezone.utc).isoformat()
    # envelope, so a cached run served from R2 looks the same as a live one.
    # "tasks" is the same run reshaped for the demand-dashboard task list; it
    # rides along here so the Worker can serve /api/tasks from the cached run
    # instead of triggering a second (multi-minute) optimizer run.
    return {
        "generated_at": generated_at,
        "assignments": output,
        "tasks": assignments_to_tasks(output, generated_at),
    }


@app.get("/tasks")
def get_tasks(
    sitting_hours: int = 6,
    w_demand: Optional[float] = None,
    w_charger: Optional[float] = None,
    w_battery: Optional[float] = None,
    w_trip: Optional[float] = None,
    w_relo: Optional[float] = None,
):
    # local/direct use only — in production the Worker serves /api/tasks from
    # the cached run rather than routing here
    run = get_assignments(sitting_hours, w_demand, w_charger, w_battery, w_trip, w_relo)
    return {"generated_at": run["generated_at"], "tasks": run["tasks"]}


@app.get("/service-area")
def service_area():
    gdf = get_home_zone_gdf_cached()
    if gdf is None or gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    return json.loads(gdf.to_json())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)