"""Routes vehicle to block and pod to charger distances and times through
Valhalla (OpenStreetMap routing), falling back to a haversine estimate
whenever a request fails or times out.

get_accurate_route does a single origin/destination lookup. build_travel_time_matrix
batches many to many lookups for the optimizer's vehicle to block scoring pass
and caches results to disk per vehicle, keyed on that vehicle's own (rounded)
lat/lon. That way only vehicles that actually moved since the last run need
fresh API calls, instead of invalidating the whole matrix on a fixed TTL.
"""

import requests
import pickle
import time
from pathlib import Path

VALHALLA_URL = "https://valhalla1.openstreetmap.de/sources_to_targets"
CACHE_DIR = Path(__file__).parent.parent / "data_files" / "_api_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TRAVEL_TIME_CACHE = CACHE_DIR / "travel_times.pkl"

# Running tally of OSM/Valhalla API usage for this process, for pricing out
# hosted/paid routing options against actual call volume.
api_call_counts = {
    "route_requests": 0,       # calls to /route
    "matrix_requests": 0,      # calls to /sources_to_targets
    "matrix_elements": 0,      # sum of len(sources) * len(targets) across those calls
}


def print_api_call_summary():
    print(
        f"[OSM API usage] /route: {api_call_counts['route_requests']} call(s) | "
        f"/sources_to_targets: {api_call_counts['matrix_requests']} call(s), "
        f"{api_call_counts['matrix_elements']} element(s)"
    )


def get_accurate_route(origin_lat, origin_lon, dest_lat, dest_lon):
    api_call_counts["route_requests"] += 1
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
    except Exception:
        # fall back to haversine
        from core_functions.scoring import haversine
        dist = haversine(origin_lat, origin_lon, dest_lat, dest_lon)
        return {
            "time_minutes": round((dist / 20) * 60, 2),
            "distance_miles": round(dist, 2)
        }


def build_travel_time_matrix(evies, blocks, batch_size=10):
    """Builds a {(vehicle_name, block_id): {time_minutes, distance_miles}} matrix
    using Valhalla's sources_to_targets endpoint.

    Caches per-vehicle, keyed on that vehicle's own (rounded) lat/lon —
    instead of invalidating the whole matrix on a fixed TTL, a vehicle's
    cached routes are reused as long as it hasn't actually moved since the
    last run. Only vehicles that have relocated need fresh API calls, which
    is normally a small fraction of the fleet between consecutive runs.
    """
    block_ids = [b["block_id"] for b in blocks]
    targets = [{"lat": b["lat"], "lon": b["lon"]} for b in blocks]

    cached_times = {}
    cached_vehicle_locations = {}
    if TRAVEL_TIME_CACHE.exists():
        try:
            with open(TRAVEL_TIME_CACHE, "rb") as f:
                cache_data = pickle.load(f)
            if isinstance(cache_data, dict) and "travel_times" in cache_data:
                cached_times = cache_data["travel_times"]
                cached_vehicle_locations = cache_data["vehicle_locations"]
        except Exception:
            pass  # corrupt or old-format cache file — treat as empty and rebuild

    travel_times = {}
    vehicles_needing_routing = []

    for v in evies:
        v_lat = round(v["location"]["latitude"], 5)
        v_lon = round(v["location"]["longitude"], 5)
        if cached_vehicle_locations.get(v["name"]) == (v_lat, v_lon):
            rows = {block_id: cached_times.get((v["name"], block_id)) for block_id in block_ids}
            if all(val is not None for val in rows.values()):
                travel_times.update({(v["name"], block_id): val for block_id, val in rows.items()})
                continue
        vehicles_needing_routing.append(v)

    if not vehicles_needing_routing:
        print("All vehicle locations unchanged since last run — reused cached travel time matrix entirely.")
        return travel_times

    reused_count = len(evies) - len(vehicles_needing_routing)
    print(
        f"Building travel time matrix ({len(vehicles_needing_routing)} vehicles need routing x "
        f"{len(blocks)} blocks; {reused_count} vehicle(s) reused from cache, unchanged)..."
    )

    new_vehicle_locations = dict(cached_vehicle_locations)

    for i in range(0, len(vehicles_needing_routing), batch_size):
        batch = vehicles_needing_routing[i : i + batch_size]
        sources = [
            {"lat": v["location"]["latitude"], "lon": v["location"]["longitude"]}
            for v in batch
        ]

        try:
            api_call_counts["matrix_requests"] += 1
            api_call_counts["matrix_elements"] += len(sources) * len(targets)
            response = requests.post(
                VALHALLA_URL,
                json={"sources": sources, "targets": targets, "costing": "auto"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            for v_idx, vehicle in enumerate(batch):
                for b_idx, block_id in enumerate(block_ids):
                    entry = data["sources_to_targets"][v_idx][b_idx]
                    if entry and entry.get("time") is not None:
                        travel_times[(vehicle["name"], block_id)] = {
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
                        travel_times[(vehicle["name"], block_id)] = {
                            "time_minutes": round((dist / 20) * 60, 2),
                            "distance_miles": round(dist, 2),
                        }
                new_vehicle_locations[vehicle["name"]] = (
                    round(vehicle["location"]["latitude"], 5),
                    round(vehicle["location"]["longitude"], 5),
                )
            print(f"  Batch {i // batch_size + 1}/{-(-len(vehicles_needing_routing) // batch_size)} done")
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
                    travel_times[(vehicle["name"], b["block_id"])] = {
                        "time_minutes": round((dist / 20) * 60, 2),
                        "distance_miles": round(dist, 2),
                    }
                new_vehicle_locations[vehicle["name"]] = (
                    round(vehicle["location"]["latitude"], 5),
                    round(vehicle["location"]["longitude"], 5),
                )

    with open(TRAVEL_TIME_CACHE, "wb") as f:
        pickle.dump(
            {"travel_times": travel_times, "vehicle_locations": new_vehicle_locations},
            f,
        )
    print("Travel time matrix cached to disk.")

    return travel_times