"""Self-check for assign_points_to_neighborhoods: every point should get a
real neighborhood name, including ones on a shared boundary edge or outside
every polygon (the "unknown"/empty destinations bug)."""

import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa

from core_functions.scoring import assign_points_to_neighborhoods
from core_functions.data_fetcher import SCRIPT_DIR

# two adjoining 1x1 squares sharing the edge x=1, plus a gap so a third point
# can land fully outside both polygons.
NEIGHBORHOODS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"BDNAME": "West"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"BDNAME": "East"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]],
            },
        },
    ],
}


def demo():
    fd, geojson_path = tempfile.mkstemp(
        suffix=".geojson", dir=os.path.join(SCRIPT_DIR, "data_files")
    )
    with os.fdopen(fd, "w") as f:
        json.dump(NEIGHBORHOODS, f)
    relative_path = os.path.join("data_files", os.path.basename(geojson_path))
    cache_path = os.path.join(
        SCRIPT_DIR, "data_files", "_neighborhood_assignment_cache_block_id.pkl"
    )

    try:
        points = [
            {"block_id": "interior", "lat": 0.5, "lon": 0.5},
            {"block_id": "boundary", "lat": 0.5, "lon": 1.0},  # on the shared edge
            {"block_id": "outside", "lat": 0.5, "lon": 5.0},  # nowhere near either square
        ]
        result = assign_points_to_neighborhoods(points, relative_path, id_key="block_id")

        assert result["interior"] == "West", result
        assert result["boundary"] in ("West", "East"), result
        assert result["outside"] == "East", result  # nearest of the two squares
        print("ok")
    finally:
        os.unlink(geojson_path)
        if os.path.exists(cache_path):
            os.unlink(cache_path)


if __name__ == "__main__":
    demo()
