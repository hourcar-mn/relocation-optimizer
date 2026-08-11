import os
import json
import pickle
import time
import requests
import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path
from shapely.geometry import Point
from sklearn.cluster import DBSCAN

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = Path(SCRIPT_DIR) / "data_files" / "_api_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
POD_CACHE = CACHE_DIR / "pods.pkl"
POD_CACHE_TTL = 86400  # 24 hours — pods don't change often

# 50m in degrees latitude (~0.00045 degrees) - 164 feet
CLUSTER_EPS_DEGREES = 0.00045
MIN_TRIPS_PER_POD = 2
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def load_pods_cached(
    trips_csv_path, block_groups_geojson_path, chargers, force_rebuild=False
):
    """
    Returns list of pod dicts, one per candidate
    parking location across the service area.
    """
    if not force_rebuild and POD_CACHE.exists():
        age = time.time() - POD_CACHE.stat().st_mtime
        if age < POD_CACHE_TTL:
            print("Using cached pods.")
            with open(POD_CACHE, "rb") as f:
                return pickle.load(f)

    print("Building pod database...")
    pods = []

    # step 1: cluster trip history into pod candidates
    trip_pods = generate_trip_pods(trips_csv_path, block_groups_geojson_path)
    pods.extend(trip_pods)
    print(f"  Trip history pods: {len(trip_pods)}")

    # step 2: add Evie charger locations as pods
    charger_pods = generate_charger_pods(chargers, block_groups_geojson_path)
    pods.extend(charger_pods)
    print(f"  Charger pods: {len(charger_pods)}")

    # step 3: add OSM amenity-adjacent pods (bus stops, etc.)
    osm_pods = generate_osm_pods(block_groups_geojson_path)
    pods.extend(osm_pods)
    print(f"  OSM pods: {len(osm_pods)}")

    # step 4: deduplicate pods within 50m of each other
    pods = deduplicate_pods(pods)
    print(f"  Total pods after deduplication: {len(pods)}")

    # step 5: add fallback centroid pods for blocks with no pods
    pods = add_centroid_fallbacks(pods, block_groups_geojson_path)
    print(f"  Total pods including centroid fallbacks: {len(pods)}")

    with open(POD_CACHE, "wb") as f:
        pickle.dump(pods, f)
    print("Pod database cached to disk.")

    return pods


def generate_trip_pods(trips_csv_path, block_groups_geojson_path):
    """
    Clusters trip start GPS coordinates within each block group
    using DBSCAN at 50m radius, min 2 trips per cluster.
    """
    trips_csv_path = os.path.join(SCRIPT_DIR, trips_csv_path)
    block_groups_geojson_path = os.path.join(SCRIPT_DIR, block_groups_geojson_path)

    trips = pd.read_csv(trips_csv_path)
    trips = trips.dropna(subset=["StartLat", "StartLong"])
    trips = trips[
        trips["StartLat"].between(-90, 90) & trips["StartLong"].between(-180, 180)
    ]

    geometry = [Point(xy) for xy in zip(trips["StartLong"], trips["StartLat"])]
    trips_gdf = gpd.GeoDataFrame(trips, geometry=geometry, crs="EPSG:4269")

    blocks = gpd.read_file(block_groups_geojson_path)
    blocks["block_id"] = blocks["block_id"].astype(str)

    trips_with_block = gpd.sjoin(
        trips_gdf, blocks[["block_id", "geometry"]], how="left", predicate="within"
    )

    pods = []
    for block_id, group in trips_with_block.groupby("block_id"):
        coords = group[["StartLat", "StartLong"]].values
        if len(coords) < MIN_TRIPS_PER_POD:
            continue

        # DBSCAN in degree space — 0.00045 degrees ≈ 50m
        db = DBSCAN(eps=CLUSTER_EPS_DEGREES, min_samples=MIN_TRIPS_PER_POD).fit(coords)
        labels = db.labels_

        for label in set(labels):
            if label == -1:
                continue  # noise points
            mask = labels == label
            cluster_points = coords[mask]
            center_lat = float(np.mean(cluster_points[:, 0]))
            center_lon = float(np.mean(cluster_points[:, 1]))
            trip_count = int(np.sum(mask))

            pods.append(
                {
                    "pod_id": f"{block_id}_trip_{label}",
                    "block_id": str(block_id),
                    "lat": center_lat,
                    "lon": center_lon,
                    "source": "trip_history",
                    "trip_count": trip_count,
                    "maps_link": f"https://www.google.com/maps?q={center_lat},{center_lon}",
                }
            )

    return pods


def generate_charger_pods(chargers, block_groups_geojson_path):
    """
    Adds each Evie charger location as a pod candidate,
    spatially joined to its block group.
    """
    block_groups_geojson_path = os.path.join(SCRIPT_DIR, block_groups_geojson_path)
    blocks = gpd.read_file(block_groups_geojson_path)
    blocks["block_id"] = blocks["block_id"].astype(str)

    charger_points = gpd.GeoDataFrame(
        {"name": [c["name"] for c in chargers]},
        geometry=[Point(c["lon"], c["lat"]) for c in chargers],
        crs="EPSG:4269",
    )

    joined = gpd.sjoin(
        charger_points, blocks[["block_id", "geometry"]], how="left", predicate="within"
    )

    pods = []
    for i, (_, row) in enumerate(joined.iterrows()):
        if pd.isna(row.get("block_id")):
            continue
        c = chargers[i]
        pods.append(
            {
                "pod_id": f"{row['block_id']}_charger_{i}",
                "block_id": str(row["block_id"]),
                "lat": c["lat"],
                "lon": c["lon"],
                "source": "charger",
                "trip_count": 0,
                "has_charger": True,
                "maps_link": f"https://www.google.com/maps?q={c['lat']},{c['lon']}",
            }
        )

    return pods


def generate_osm_pods(block_groups_geojson_path):
    """
    Queries Overpass API for bus stops, transit stations within
    the service area bounding box. Each becomes a pod candidate
    since these locations attract organic foot traffic.
    """
    block_groups_geojson_path = os.path.join(SCRIPT_DIR, block_groups_geojson_path)
    blocks = gpd.read_file(block_groups_geojson_path)
    blocks["block_id"] = blocks["block_id"].astype(str)

    # service area bounding box
    bounds = blocks.total_bounds  # minx, miny, maxx, maxy
    bbox = f"{bounds[1]},{bounds[0]},{bounds[3]},{bounds[2]}"

    query = f"""
    [out:json][timeout:60];
    (
      node["highway"="bus_stop"]({bbox});
      node["public_transport"="stop_position"]({bbox});
      node["railway"="station"]({bbox});
      node["railway"="tram_stop"]({bbox});
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
        print(f"  OSM returned {len(elements)} transit nodes")
    except Exception as e:
        print(f"  OSM query failed: {e}")
        return []

    osm_points = gpd.GeoDataFrame(
        {"osm_id": [e["id"] for e in elements]},
        geometry=[Point(e["lon"], e["lat"]) for e in elements],
        crs="EPSG:4269",
    )

    joined = gpd.sjoin(
        osm_points, blocks[["block_id", "geometry"]], how="left", predicate="within"
    )

    pods = []
    for i, (_, row) in enumerate(joined.iterrows()):
        if pd.isna(row.get("block_id")):
            continue
        e = elements[i]
        pods.append(
            {
                "pod_id": f"{row['block_id']}_osm_{e['id']}",
                "block_id": str(row["block_id"]),
                "lat": e["lat"],
                "lon": e["lon"],
                "source": "osm_transit",
                "trip_count": 0,
                "maps_link": f"https://www.google.com/maps?q={e['lat']},{e['lon']}",
            }
        )

    return pods


def deduplicate_pods(pods):
    """
    Removes pod candidates within 50m of each other,
    keeping the one with the higher trip_count.
    Priority: trip_history > charger > osm_transit
    """
    if not pods:
        return pods

    SOURCE_PRIORITY = {"trip_history": 0, "charger": 1, "osm_transit": 2}

    # sort by priority then trip count descending
    pods_sorted = sorted(
        pods,
        key=lambda p: (SOURCE_PRIORITY.get(p["source"], 99), -p.get("trip_count", 0)),
    )

    kept = []
    for pod in pods_sorted:
        too_close = False
        for kept_pod in kept:
            if kept_pod["block_id"] != pod["block_id"]:
                continue
            dlat = pod["lat"] - kept_pod["lat"]
            dlon = pod["lon"] - kept_pod["lon"]
            dist_deg = (dlat**2 + dlon**2) ** 0.5
            if dist_deg < CLUSTER_EPS_DEGREES:
                too_close = True
                break
        if not too_close:
            kept.append(pod)

    return kept


def add_centroid_fallbacks(pods, block_groups_geojson_path):
    """
    For any block group with no pod candidates at all,
    adds the block centroid as a fallback pod.
    """
    block_groups_geojson_path = os.path.join(SCRIPT_DIR, block_groups_geojson_path)

    with open(block_groups_geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    covered_block_ids = {p["block_id"] for p in pods}

    for feature in data["features"]:
        props = feature["properties"]
        block_id = str(props.get("block_id"))
        if block_id in covered_block_ids:
            continue
        lat = float(props.get("INTPTLAT") or props.get("centroidLat", 0))
        lon = float(props.get("INTPTLON") or props.get("centroidLon", 0))
        if lat == 0 and lon == 0:
            continue
        pods.append(
            {
                "pod_id": f"{block_id}_centroid",
                "block_id": block_id,
                "lat": lat,
                "lon": lon,
                "source": "centroid_fallback",
                "trip_count": 0,
                "maps_link": f"https://www.google.com/maps?q={lat},{lon}",
            }
        )

    return pods
