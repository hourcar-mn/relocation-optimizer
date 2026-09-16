"""Scores how close each block is to an available charger, in drive time.

Routes the nearest few chargers (by haversine prefilter) for every block
through apis/open_street_map.get_accurate_route, then converts the best drive
time into a 0-100 score that decays to 0 at CHARGER_SCORE_DECAY_MINUTES.
Results are cached to disk, keyed on a signature of the block ids and charger
locations, so a run only re-routes when blocks or chargers actually changed.
"""

import os
import pickle
import hashlib
import concurrent.futures
import logging
import time
from core_functions.data_fetcher import SCRIPT_DIR
from apis.open_street_map import get_accurate_route
from core_functions.config import (
    AVG_CITY_SPEED_MPH,
    CHARGER_SCORE_DECAY_MINUTES,
    CHARGER_HAVERSINE_PREFILTER_CANDIDATES,
    haversine,
)

log = logging.getLogger(__name__)

_charger_route_cache = {}
_CHARGER_SCORE_CACHE_PATH = os.path.join(
    SCRIPT_DIR, "data_files", "_charger_score_cache.pkl"
)
CHARGER_SCORE_CACHE_TTL = 600  # seconds, match get_charger_status_cached's typical ttl


def _get_charger_drive_time(b_lat, b_lon, c_lat, c_lon):
    # per-pair cache to avoid duplicate routing calls within one run
    key = (round(b_lat, 5), round(b_lon, 5), round(c_lat, 5), round(c_lon, 5))
    if key in _charger_route_cache:
        return _charger_route_cache[key]

    drive_time = None
    try:
        route = get_accurate_route(b_lat, b_lon, c_lat, c_lon)
        if route and route.get("time_minutes") is not None:
            drive_time = route["time_minutes"]
    except Exception:
        drive_time = None

    _charger_route_cache[key] = drive_time
    return drive_time


def get_charger_score(block, nearest_chargers):
    b_lat = block["lat"]
    b_lon = block["lon"]
    if not nearest_chargers:
        return 0.0, None

    best_drive_time = None
    for c in nearest_chargers:
        drive_time = _get_charger_drive_time(b_lat, b_lon, c["lat"], c["lon"])
        if drive_time is not None and (
            best_drive_time is None or drive_time < best_drive_time
        ):
            best_drive_time = drive_time

    if best_drive_time is None:
        nearest_dist = haversine(
            b_lat, b_lon, nearest_chargers[0]["lat"], nearest_chargers[0]["lon"]
        )
        best_drive_time = (nearest_dist / AVG_CITY_SPEED_MPH) * 60

    score = max(0.0, (1 - best_drive_time / CHARGER_SCORE_DECAY_MINUTES) * 100)
    return round(score, 2), round(best_drive_time, 2)


def _charger_signature(chargers):
    # location-only fingerprint, ignores live plug counts so cache doesn't churn
    sig = sorted((round(c["lat"], 4), round(c["lon"], 4)) for c in chargers)
    return hashlib.md5(str(sig).encode()).hexdigest()


def _precompute_nearest_chargers(blocks, available_chargers):
    nearest_by_block = {}
    for b in blocks:
        candidates = sorted(
            available_chargers,
            key=lambda c: haversine(b["lat"], b["lon"], c["lat"], c["lon"]),
        )[:CHARGER_HAVERSINE_PREFILTER_CANDIDATES]
        nearest_by_block[b["block_id"]] = candidates
    return nearest_by_block


def precompute_charger_scores(blocks, chargers):
    block_ids = tuple(sorted(b["block_id"] for b in blocks))
    signature = (block_ids, _charger_signature(chargers))
    if os.path.exists(_CHARGER_SCORE_CACHE_PATH):
        try:
            age = time.time() - os.path.getmtime(_CHARGER_SCORE_CACHE_PATH)
            if age < CHARGER_SCORE_CACHE_TTL:
                with open(_CHARGER_SCORE_CACHE_PATH, "rb") as f:
                    cached_sig, cached_scores, cached_times = pickle.load(f)
                if cached_sig == signature:
                    print("Using cached charger scores.")
                    return cached_scores, cached_times
        except Exception:
            pass

    nearest_by_block = _precompute_nearest_chargers(blocks, chargers)

    def _score_one(b):
        return b["block_id"], get_charger_score(b, nearest_by_block[b["block_id"]])

    scores = {}
    times = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        for block_id, (score, drive_time) in executor.map(_score_one, blocks):
            scores[block_id] = score
            times[block_id] = drive_time

    try:
        os.makedirs(os.path.dirname(_CHARGER_SCORE_CACHE_PATH), exist_ok=True)
        with open(_CHARGER_SCORE_CACHE_PATH, "wb") as f:
            pickle.dump((signature, scores, times), f)
    except Exception as e:
        log.error(f"Failed to write charger score cache: {e}")

    return scores, times
