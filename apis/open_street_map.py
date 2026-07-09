import requests
import pickle
import time
import os
from pathlib import Path

VALHALLA_URL = "https://valhalla1.openstreetmap.de/sources_to_targets"
CACHE_DIR = Path(__file__).parent.parent / "data_files" / "_api_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TRAVEL_TIME_CACHE = CACHE_DIR / "travel_times.pkl"
TRAVEL_TIME_TTL = 3600  # 1 hour

def get_accurate_route(origin_lat, origin_lon, dest_lat, dest_lon):
   
    try:
        response = requests.post(
            "https://valhalla1.openstreetmap.de/route",
            json={
                "locations": [
                    {"lat": origin_lat, "lon": origin_lon, "type": "break"},
                    {"lat": dest_lat, "lon": dest_lon, "type": "break"}
                ],
                "costing": "auto",
                "directions_options": {"units": "miles"}
            },
            headers={"User-Agent": "HourCar-Evie-Optimizer/1.0"},
            timeout=10
        )
        response.raise_for_status()
        summary = response.json()["trip"]["summary"]
        return {
            "time_minutes": round(summary["time"] / 60, 2),
            "distance_miles": round(summary["length"], 2)
        }
    except Exception as e:
        # fall back to haversine
        from core_functions.scoring import haversine
        dist = haversine(origin_lat, origin_lon, dest_lat, dest_lon)
        return {
            "time_minutes": round((dist / 20) * 60, 2),
            "distance_miles": round(dist, 2)
        }

#  Builds a {(vehicle_name, geoid): drive_time_minutes} matrix using Valhalla's sources_to_targets endpoint. 
# Sends vehicles in batches to avoid hitting API limits — much faster than individual route calls.
def build_travel_time_matrix(evies, blocks, batch_size=10):

    # check disk cache first
    if TRAVEL_TIME_CACHE.exists():
        age = time.time() - TRAVEL_TIME_CACHE.stat().st_mtime
        if age < TRAVEL_TIME_TTL:
            print("Using cached travel time matrix.")
            with open(TRAVEL_TIME_CACHE, "rb") as f:
                return pickle.load(f)

    print(
        f"Building travel time matrix ({len(evies)} vehicles x {len(blocks)} blocks)..."
    )
    travel_times = {}

    # build target list once — all block centroids
    targets = [{"lat": b["lat"], "lon": b["lon"]} for b in blocks]
    block_geoids = [b["geoid"] for b in blocks]

    # batch vehicles
    for i in range(0, len(evies), batch_size):
        batch = evies[i : i + batch_size]
        sources = [
            {"lat": v["location"]["latitude"], "lon": v["location"]["longitude"]}
            for v in batch
        ]

        try:
            response = requests.post(
                VALHALLA_URL,
                json={"sources": sources, "targets": targets, "costing": "auto"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            # data["sources_to_targets"] is a list of lists:
            # outer index = source (vehicle), inner index = target (block)
            for v_idx, vehicle in enumerate(batch):
                for b_idx, geoid in enumerate(block_geoids):
                    entry = data["sources_to_targets"][v_idx][b_idx]
                    # time is in seconds, convert to minutes
                    # entry is None if no route found
                    if entry and entry.get("time") is not None:
                        travel_times[(vehicle["name"], geoid)] = {
                            "time_minutes": round(entry["time"] / 60, 2),
                            "distance_miles": round(
                                entry.get("distance", 0) * 0.621371, 2
                            ),
                        }
                    else:
                        from core_functions.scoring import haversine

                        dist = haversine(
                            sources[v_idx]["lat"],
                            sources[v_idx]["lon"],
                            blocks[b_idx]["lat"],
                            blocks[b_idx]["lon"],
                        )
                        travel_times[(vehicle["name"], geoid)] = {
                            "time_minutes": round((dist / 20) * 60, 2),
                            "distance_miles": round(dist, 2),
                        }
            print(f"  Batch {i // batch_size + 1}/{-(-len(evies) // batch_size)} done")
            # small delay to be polite to the public server
            time.sleep(0.5)

        except Exception as e:
            print(
                f"  Batch {i // batch_size + 1} failed: {e} — falling back to haversine"
            )
            from core_functions.scoring import haversine

            for vehicle in batch:
                for b in blocks:
                    dist = haversine(
                        vehicle["location"]["latitude"],
                        vehicle["location"]["longitude"],
                        b["lat"],
                        b["lon"],
                    )
                    travel_times[(vehicle["name"], b["geoid"])] = round(
                        (dist / 20) * 60, 2
                    )

    # save to disk
    with open(TRAVEL_TIME_CACHE, "wb") as f:
        pickle.dump(travel_times, f)
    print("Travel time matrix cached to disk.")

    return travel_times
