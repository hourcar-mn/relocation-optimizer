"""Routes each prelim assignment's vehicle to its chosen drop-off pod for
real drive time/distance, caching results to disk keyed on rounded
coordinates so only genuinely new vehicle/pod pairs hit the routing API.
"""

import os
import pickle
import logging
import time
import concurrent.futures
from core_functions.data_fetcher import SCRIPT_DIR
from core_functions.config import MAX_RELOCATION_RADIUS_MILES
from apis.open_street_map import get_accurate_route

log = logging.getLogger(__name__)

_POD_ROUTE_CACHE_PATH = os.path.join(SCRIPT_DIR, "data_files", "_pod_route_cache.pkl")


def _load_pod_route_cache():
    if os.path.exists(_POD_ROUTE_CACHE_PATH):
        try:
            with open(_POD_ROUTE_CACHE_PATH, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return {}


def _save_pod_route_cache(cache):
    try:
        os.makedirs(os.path.dirname(_POD_ROUTE_CACHE_PATH), exist_ok=True)
        with open(_POD_ROUTE_CACHE_PATH, "wb") as f:
            pickle.dump(cache, f)
    except Exception as e:
        log.error(f"Failed to write pod route cache: {e}")


def _cache_key(item):
    if not (
        item["current_lat"]
        and item["current_lon"]
        and item["drop_off_lat"]
        and item["drop_off_lon"]
    ):
        return None
    return (
        round(item["current_lat"], 5),
        round(item["current_lon"], 5),
        round(item["drop_off_lat"], 5),
        round(item["drop_off_lon"], 5),
    )


def _route_for_item(item):
    try:
        route = get_accurate_route(
            item["current_lat"],
            item["current_lon"],
            item["drop_off_lat"],
            item["drop_off_lon"],
        )
        return route["time_minutes"], route["distance_miles"]
    except Exception:
        return None, None


def route_prelim_to_pods(prelim):
    # route vehicle -> pod concurrently, cached by rounded coords
    t0 = time.time()
    pod_route_cache = _load_pod_route_cache()
    cache_hits = 0

    items_needing_routing = []
    for item in prelim:
        key = _cache_key(item)
        if key is not None and key in pod_route_cache:
            item["drive_time"], item["drive_distance"] = pod_route_cache[key]
            cache_hits += 1
        elif key is not None and item["drop_off_maps_link"]:
            items_needing_routing.append(item)

    if items_needing_routing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(_route_for_item, items_needing_routing))

        for item, (drive_time, drive_distance) in zip(items_needing_routing, results):
            item["drive_time"] = drive_time
            item["drive_distance"] = drive_distance
            key = _cache_key(item)
            if key is not None and drive_time is not None:
                pod_route_cache[key] = (drive_time, drive_distance)

        _save_pod_route_cache(pod_route_cache)

    print(
        f"[TIMING] vehicle-to-pod routing: {time.time() - t0:.2f}s ({cache_hits} cached, {len(items_needing_routing)} routed)"
    )

    # enforce the real distance cap here, against the actual routed vehicle ->
    # pod trip -- the earlier haversine/centroid check (MAX_RELOCATION_RADIUS_MILES,
    # optimizer.py's main scoring loop) only bounds the block's centroid, and the
    # pod selected within a block can sit well past that toward the block's far
    # edge, so this is the one place that reflects the true trip distance.
    # Items where routing failed (drive_distance is None) are left alone --
    # unknown isn't the same as over the cap.
    before_distance_filter = len(prelim)
    prelim = [
        item
        for item in prelim
        if item["drive_distance"] is None
        or item["drive_distance"] <= MAX_RELOCATION_RADIUS_MILES
    ]
    skipped_too_far = before_distance_filter - len(prelim)
    if skipped_too_far:
        print(
            f"Skipped {skipped_too_far} assignment(s): pod routed distance exceeded "
            f"{MAX_RELOCATION_RADIUS_MILES}mi cap"
        )
    return prelim
