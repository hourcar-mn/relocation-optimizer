"""Stage 2 of the two stage relocation pick: given a block group the MILP
already assigned a vehicle to, choose the single best pod within it and
format that choice for display.

select_best_pod combines each pod's micro score (microlocation/micro_scorer.py)
with its historical trip count, and excludes any pod within 1 mile of the
vehicle's current location so a "relocation" can't just be a trivial nudge
down the block. format_pod_output and build_nearby_summary turn the winning
pod into the human readable maps link and "nearby: X, Y" string optimizer.py
attaches to each assignment.
"""

from core_functions.config import haversine_meters

# weight of micro score vs historical trip count in pod selection
# within a block group, micro signals drive the final pick
MICRO_WEIGHT = 0.60
HISTORICAL_WEIGHT = 0.40

# pods closer than this to the vehicle's current location aren't a real
# relocation, so they're excluded from consideration entirely
MIN_POD_DISTANCE_MILES = 1.0
METERS_PER_MILE = 1609.34


def select_best_pod(block_id, pods, micro_scores, vehicle=None):
    """
    Stage 2: given a block group assignment from the MILP,
    pick the best specific pod within that block.

    Returns the selected pod dict, or None if no pods exist
    (caller should fall back to block centroid).
    """
    block_pods = [p for p in pods if p["block_id"] == block_id]

    if vehicle:
        veh_lat = vehicle.get("location", {}).get("latitude")
        veh_lon = vehicle.get("location", {}).get("longitude")
        if veh_lat and veh_lon:
            block_pods = [
                p
                for p in block_pods
                if haversine_meters(veh_lat, veh_lon, p["lat"], p["lon"])
                / METERS_PER_MILE
                >= MIN_POD_DISTANCE_MILES
            ]

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
