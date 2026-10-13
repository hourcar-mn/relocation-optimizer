"""Self-checks for pod dedup and the home-zone filter.

Both have produced real bugs: duplicate pods a block apart, and pods placed
outside the service area. Neither fails loudly at runtime — the optimizer just
returns a worse or out-of-zone drop-off. Run: python tests/test_pods.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa

import geopandas as gpd
from shapely.geometry import Polygon
from microlocation.pod_generator import (
    deduplicate_pods,
    filter_pods_to_home_zone,
    CLUSTER_EPS_DEGREES,
)

LAT, LON = 44.98, -93.27
NEAR = CLUSTER_EPS_DEGREES / 4      # well inside the 50m cluster radius
FAR = CLUSTER_EPS_DEGREES * 10      # well outside it


def pod(pod_id, block_id, dlat=0.0, source="trip_history", trip_count=0):
    return {
        "pod_id": pod_id,
        "block_id": block_id,
        "lat": LAT + dlat,
        "lon": LON,
        "source": source,
        "trip_count": trip_count,
    }


def test_empty_input_is_returned_unchanged():
    assert deduplicate_pods([]) == []
    assert filter_pods_to_home_zone([], None) == []


def test_trip_history_beats_osm_when_pods_overlap():
    kept = deduplicate_pods([
        pod("osm", "A", source="osm_transit", trip_count=99),
        pod("trip", "A", dlat=NEAR, source="trip_history", trip_count=1),
    ])
    assert [p["pod_id"] for p in kept] == ["trip"], kept


def test_higher_trip_count_wins_within_a_source():
    kept = deduplicate_pods([
        pod("quiet", "A", trip_count=3),
        pod("busy", "A", dlat=NEAR, trip_count=10),
    ])
    assert [p["pod_id"] for p in kept] == ["busy"], kept


def test_pods_far_apart_are_both_kept():
    kept = deduplicate_pods([
        pod("first", "A", trip_count=10),
        pod("second", "A", dlat=FAR, trip_count=3),
    ])
    assert {p["pod_id"] for p in kept} == {"first", "second"}, kept


def test_nearby_pods_in_different_blocks_are_both_kept():
    # dedup is per block — two blocks can legitimately have pods across a street
    kept = deduplicate_pods([
        pod("in_a", "A", trip_count=10),
        pod("in_b", "B", dlat=NEAR, trip_count=3),
    ])
    assert {p["pod_id"] for p in kept} == {"in_a", "in_b"}, kept


def test_home_zone_filter_drops_outside_pods():
    zone = gpd.GeoDataFrame(
        geometry=[Polygon([
            (LON - 0.01, LAT - 0.01),
            (LON + 0.01, LAT - 0.01),
            (LON + 0.01, LAT + 0.01),
            (LON - 0.01, LAT + 0.01),
        ])],
        crs="EPSG:4326",
    )
    inside = pod("inside", "A")
    outside = pod("outside", "A", dlat=0.5)
    kept = filter_pods_to_home_zone([inside, outside], zone)
    assert [p["pod_id"] for p in kept] == ["inside"], kept


if __name__ == "__main__":
    test_empty_input_is_returned_unchanged()
    test_trip_history_beats_osm_when_pods_overlap()
    test_higher_trip_count_wins_within_a_source()
    test_pods_far_apart_are_both_kept()
    test_nearby_pods_in_different_blocks_are_both_kept()
    test_home_zone_filter_drops_outside_pods()
    print("ok")
