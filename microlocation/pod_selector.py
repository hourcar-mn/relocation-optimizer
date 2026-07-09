import os
import math
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# weight of micro score vs historical trip count in pod selection
# within a block group, micro signals drive the final pick
MICRO_WEIGHT = 0.60
HISTORICAL_WEIGHT = 0.40


def select_best_pod(block_geoid, pods, micro_scores, vehicle=None):
    """
    Stage 2: given a block group assignment from the MILP,
    pick the best specific pod within that block.

    Returns the selected pod dict, or None if no pods exist
    (caller should fall back to block centroid).
    """
    block_pods = [p for p in pods if p["geoid"] == block_geoid]

    if not block_pods:
        return None

    if len(block_pods) == 1:
        return block_pods[0]

    # score each pod within the block
    max_trips = max((p.get("trip_count", 0) for p in block_pods), default=1) or 1

    best_pod = None
    best_score = -1

    for pod in block_pods:
        micro = micro_scores.get(pod["pod_id"], 0)
        historical = min(pod.get("trip_count", 0) / max_trips * 100, 100)
        pod_score = micro * MICRO_WEIGHT + historical * HISTORICAL_WEIGHT

        if pod_score > best_score:
            best_score = pod_score
            best_pod = pod

    return best_pod


def format_pod_output(pod, micro_scores, amenity_data=None):
    """
    Formats the pod selection for human-readable output.
    Returns a dict of display strings.
    """
    if pod is None:
        return {
            "maps_link": None,
            "micro_score": 0,
            "trip_count": 0,
            "source": "unknown",
            "nearby_summary": "No pod data available"
        }

    micro_score = micro_scores.get(pod["pod_id"], 0)
    trip_count = pod.get("trip_count", 0)
    source = pod.get("source", "unknown")

    nearby_summary = build_nearby_summary(pod["lat"], pod["lon"], amenity_data)

    return {
        "maps_link": pod["maps_link"],
        "micro_score": micro_score,
        "trip_count": trip_count,
        "source": source,
        "nearby_summary": nearby_summary,
        "lat": pod["lat"],
        "lon": pod["lon"],
    }


def format_distance(dist_meters):
    dist_feet = dist_meters * 3.28084
    if dist_feet < 500:
        return f"{round(dist_feet)} ft"
    else:
        dist_miles = dist_meters / 1609.34
        return f"{round(dist_miles, 1)} mi"


def build_nearby_summary(lat, lon, amenity_data, radius_meters=167):
    if not amenity_data:
        return ""

    TYPE_LABELS = {
        "bus_stop": "Bus stop",
        "stop_position": "Transit stop",
        "station": "Train station",
        "tram_stop": "Tram stop",
        "cafe": "Café",
        "restaurant": "Restaurant",
        "supermarket": "Grocery store",
        "convenience": "Convenience store",
        "university": "University",
        "college": "College",
        "stop": "Transit stop",
        "shelter": "Bus shelter",
        "clothes": "Clothing store",
        "books": "Bookstore",
    }

    nearby = []
    for a in amenity_data:
        dist_meters = haversine_meters(lat, lon, a["lat"], a["lon"])
        if dist_meters <= radius_meters:
            label = TYPE_LABELS.get(a["type"], a["type"].replace("_", " ").title())
            nearby.append((dist_meters, label))

    if not nearby:
        return "No amenities within 2 min walk"

    nearby.sort(key=lambda x: x[0])
    return " | ".join(
        f"{label} ({format_distance(dist)})" for dist, label in nearby[:3]
    )


def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.asin(math.sqrt(a))
