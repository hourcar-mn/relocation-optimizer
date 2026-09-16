"""Spatial-joins blocks, vehicles, and pods to neighborhood polygons, and
derives each neighborhood's vehicle capacity from its average demand score.

assign_points_to_neighborhoods is the shared primitive (block groups, vehicles,
and pods all get their own lat/lon joined independently, since a block's
centroid, a vehicle sitting in it, and a pod placed within it can each land in
a different neighborhood polygon near a boundary). get_neighborhood_capacity_map
turns those assignments into optimizer.py's per-neighborhood MILP caps.
"""

import os
import pickle
import logging
from collections import defaultdict
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from core_functions.data_fetcher import SCRIPT_DIR
from core_functions.scoring import get_demand_score

log = logging.getLogger(__name__)

_NEIGHBORHOOD_ASSIGNMENT_CACHE_PATH = os.path.join(
    SCRIPT_DIR, "data_files", "_neighborhood_assignment_cache.pkl"
)
# Bump this when the join logic below changes (predicate, fallback, etc.) so
# stale caches on disk from before the change don't silently keep serving
# old results — the signature check has no other way to know the code moved.
_NEIGHBORHOOD_ASSIGNMENT_CACHE_VERSION = 2


def get_neighborhood_capacity(demand_score):
    if demand_score >= 70:
        return 4
    elif demand_score >= 40:
        return 3
    elif demand_score >= 15:
        return 2
    return 1


def assign_points_to_neighborhoods(points, neighborhoods_geojson_path, id_key="block_id"):
    """Spatial-joins each point's own lat/lon against the neighborhood polygons.

    Used for both block groups (id_key="block_id") and pods (id_key="pod_id") —
    a block group's centroid and a pod sitting in that block group can fall in
    different neighborhood polygons, so callers that need the pod's real
    neighborhood must pass pods here rather than reusing the block assignment.
    """
    ids = tuple(sorted(p[id_key] for p in points))
    cache_path = (
        _NEIGHBORHOOD_ASSIGNMENT_CACHE_PATH
        if id_key == "block_id"
        else _NEIGHBORHOOD_ASSIGNMENT_CACHE_PATH.replace(".pkl", f"_{id_key}.pkl")
    )
    signature = (ids, neighborhoods_geojson_path, _NEIGHBORHOOD_ASSIGNMENT_CACHE_VERSION)

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                cached_sig, cached_result = pickle.load(f)
            if cached_sig == signature:
                print(f"Using cached {id_key}-to-neighborhood assignments.")
                return cached_result
        except Exception:
            pass

    neighborhoods_geojson_path_full = os.path.join(
        SCRIPT_DIR, neighborhoods_geojson_path
    )

    point_gdf = gpd.GeoDataFrame(
        {id_key: [p[id_key] for p in points]},
        geometry=[Point(p["lon"], p["lat"]) for p in points],
        crs="EPSG:4269",
    )

    neighborhoods = gpd.read_file(neighborhoods_geojson_path_full)
    if neighborhoods.crs != point_gdf.crs:
        neighborhoods = neighborhoods.to_crs(point_gdf.crs)

    # "within" misses points that land exactly on a neighborhood boundary
    # (e.g. a pod snapped to a street that traces the edge) — shapely doesn't
    # count the boundary as "within" the polygon, so those points came back
    # with no neighborhood at all. "intersects" catches boundary points too.
    joined = gpd.sjoin(
        point_gdf,
        neighborhoods[["BDNAME", "geometry"]],
        how="left",
        predicate="intersects",
    ).drop_duplicates(subset=id_key)
    result = {
        pid: (name if pd.notna(name) else None)
        for pid, name in zip(joined[id_key], joined["BDNAME"])
    }

    # anything still unmatched is genuinely outside every polygon (gaps in
    # coverage, or a point just past the service area edge) — snap it to
    # whichever neighborhood is physically closest instead of leaving it blank.
    missing_ids = [pid for pid, name in result.items() if name is None]
    if missing_ids:
        missing_gdf = point_gdf[point_gdf[id_key].isin(missing_ids)]
        nearest = gpd.sjoin_nearest(missing_gdf, neighborhoods[["BDNAME", "geometry"]])
        for pid, name in zip(nearest[id_key], nearest["BDNAME"]):
            result[pid] = name

    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump((signature, result), f)
    except Exception as e:
        log.error(f"Failed to write neighborhood assignment cache: {e}")

    return result


def assign_blocks_to_neighborhoods(blocks, neighborhoods_geojson_path):
    return assign_points_to_neighborhoods(blocks, neighborhoods_geojson_path, id_key="block_id")


def get_neighborhood_capacity_map(blocks, block_id_to_neighborhood):
    neighborhood_demand = defaultdict(list)
    for b in blocks:
        nbhd = block_id_to_neighborhood.get(b["block_id"])
        if nbhd:
            neighborhood_demand[nbhd].append(get_demand_score(b))

    return {
        nbhd: get_neighborhood_capacity(sum(scores) / len(scores))
        for nbhd, scores in neighborhood_demand.items()
    }
