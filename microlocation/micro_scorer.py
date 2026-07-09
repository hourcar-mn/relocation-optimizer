import os
import pickle
import time
import requests
import math
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = Path(SCRIPT_DIR) / "data_files" / "_api_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MICRO_SCORE_CACHE = CACHE_DIR / "micro_scores.pkl"
MICRO_SCORE_TTL = 86400  # 24 hours

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 2 minute walk at 5km/h ≈ 167m
AMENITY_RADIUS_METERS = 167

# signal weights within micro score (must sum to 1.0)
HISTORICAL_WEIGHT = 0.40
TRANSIT_WEIGHT = 0.25
AMENITY_WEIGHT = 0.20
STREET_WEIGHT = 0.15

# road types scored high to low visibility
STREET_SCORES = {
    "primary": 100,
    "secondary": 85,
    "tertiary": 70,
    "residential": 40,
    "service": 20,
    "unclassified": 30,
}


def load_micro_scores_cached(pods, force_rebuild=False):
    """
    Computes and caches micro scores for all pods.
    Returns {pod_id: micro_score (0-100)}
    """
    if not force_rebuild and MICRO_SCORE_CACHE.exists():
        age = time.time() - MICRO_SCORE_CACHE.stat().st_mtime
        if age < MICRO_SCORE_TTL:
            print("Using cached micro scores.")
            with open(MICRO_SCORE_CACHE, "rb") as f:
                return pickle.load(f)

    print(f"Computing micro scores for {len(pods)} pods...")
    micro_scores = {}

    # normalize trip counts across all pods for historical signal
    max_trips = max((p.get("trip_count", 0) for p in pods), default=1) or 1

    # batch OSM queries by bounding box to avoid 1 query per pod
    amenity_data = fetch_all_amenities(pods)
    street_data = fetch_all_street_types(pods)

    for i, pod in enumerate(pods):
        pod_id = pod["pod_id"]
        lat = pod["lat"]
        lon = pod["lon"]

        # 1. historical conversion score (strongest signal)
        trip_count = pod.get("trip_count", 0)
        historical_score = min(trip_count / max_trips * 100, 100)

        # 2. transit proximity score
        transit_score = score_transit_proximity(lat, lon, amenity_data)

        # 3. amenity count score
        amenity_score = score_amenity_count(lat, lon, amenity_data)

        # 4. street visibility score
        street_score = street_data.get(pod_id, 40)

        # weighted combination
        micro_score = (
            historical_score * HISTORICAL_WEIGHT
            + transit_score * TRANSIT_WEIGHT
            + amenity_score * AMENITY_WEIGHT
            + street_score * STREET_WEIGHT
        )

        micro_scores[pod_id] = round(micro_score, 2)

        if (i + 1) % 100 == 0:
            print(f"  Scored {i + 1}/{len(pods)} pods...")

    with open(MICRO_SCORE_CACHE, "wb") as f:
        pickle.dump(micro_scores, f)
    print("Micro scores cached to disk.")

    return micro_scores


def fetch_all_amenities(pods):
    """
    Single Overpass query for all amenities across the service area.
    Returns list of {lat, lon, type} dicts.
    """
    if not pods:
        return []

    lats = [p["lat"] for p in pods]
    lons = [p["lon"] for p in pods]
    bbox = f"{min(lats)},{min(lons)},{max(lats)},{max(lons)}"

    query = f"""
    [out:json][timeout:90];
    (
      node["amenity"~"cafe|restaurant|bar|supermarket|convenience|pharmacy|bank"]({bbox});
      node["shop"~"supermarket|convenience|bakery|clothes|books"]({bbox});
      node["highway"="bus_stop"]({bbox});
      node["public_transport"="stop_position"]({bbox});
      node["railway"~"station|tram_stop|subway_entrance"]({bbox});
      node["amenity"="university"]({bbox});
      node["amenity"="college"]({bbox});
    );
    out center;
    """

    try:
        response = requests.post(
            OVERPASS_URL,
            data=query,
            timeout=60,
            headers={"User-Agent": "HourCar-Evie-Optimizer/1.0"},
        )
        response.raise_for_status()
        elements = response.json().get("elements", [])
        print(f"  Fetched {len(elements)} amenity nodes from OSM")
        return [
            {
                "lat": e["lat"],
                "lon": e["lon"],
                "type": e.get("tags", {}).get("amenity")
                or e.get("tags", {}).get("shop")
                or e.get("tags", {}).get("highway")
                or e.get("tags", {}).get("railway", "unknown"),
            }
            for e in elements
        ]
    except Exception as ex:
        print(f"  Amenity fetch failed: {ex}")
        return []


def fetch_all_street_types(pods):
    """
    Queries OSM for the nearest road type to each pod.
    Returns {pod_id: street_score}
    """
    if not pods:
        return {}

    lats = [p["lat"] for p in pods]
    lons = [p["lon"] for p in pods]
    bbox = f"{min(lats)},{min(lons)},{max(lats)},{max(lons)}"

    query = f"""
    [out:json][timeout:90];
    way["highway"~"primary|secondary|tertiary|residential|service|unclassified"]({bbox});
    out center;
    """

    try:
        response = requests.post(
            OVERPASS_URL,
            data=query,
            timeout=90,
            headers={"User-Agent": "HourCar-Evie-Optimizer/1.0"},
        )
        response.raise_for_status()
        ways = response.json().get("elements", [])
    except Exception as ex:
        print(f"  Street type fetch failed: {ex}")
        return {p["pod_id"]: 40 for p in pods}

    # for each pod, find nearest way center and get its road type
    street_scores = {}
    for pod in pods:
        nearest_score = 40  # default residential
        nearest_dist = float("inf")
        for way in ways:
            if "center" not in way:
                continue
            dlat = pod["lat"] - way["center"]["lat"]
            dlon = pod["lon"] - way["center"]["lon"]
            dist = math.sqrt(dlat**2 + dlon**2)
            if dist < nearest_dist:
                nearest_dist = dist
                road_type = way.get("tags", {}).get("highway", "residential")
                nearest_score = STREET_SCORES.get(road_type, 30)
        street_scores[pod["pod_id"]] = nearest_score

    return street_scores


def score_transit_proximity(lat, lon, amenity_data):
    """
    Scores how close the pod is to the nearest transit stop.
    100 = within 50m, decays to 0 at 400m.
    """
    transit_types = {
        "bus_stop",
        "stop_position",
        "station",
        "tram_stop",
        "subway_entrance",
    }
    transit_nodes = [a for a in amenity_data if a["type"] in transit_types]

    if not transit_nodes:
        return 0.0

    nearest_dist = min(
        haversine_meters(lat, lon, a["lat"], a["lon"]) for a in transit_nodes
    )

    # 100 at 0m, 0 at 400m (2 min walk threshold)
    score = max(0, (1 - nearest_dist / 400) * 100)
    return round(score, 2)


def score_amenity_count(lat, lon, amenity_data):
    """
    Counts cafés, restaurants, shops etc. within 167m (2 min walk).
    Normalized to 0-100 with cap at 10 amenities = 100.
    """
    food_types = {
        "cafe",
        "restaurant",
        "bar",
        "supermarket",
        "convenience",
        "pharmacy",
        "bank",
        "bakery",
        "clothes",
        "books",
        "university",
        "college",
    }

    count = sum(
        1
        for a in amenity_data
        if a["type"] in food_types
        and haversine_meters(lat, lon, a["lat"], a["lon"]) <= AMENITY_RADIUS_METERS
    )

    score = min(count / 10 * 100, 100)
    return round(score, 2)


def haversine_meters(lat1, lon1, lat2, lon2):
    """Returns distance in meters between two lat/lon points."""
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))
