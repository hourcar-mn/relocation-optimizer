"""Tunable constants shared across the optimizer: score weights, eligibility
thresholds, and cost model assumptions, plus the haversine helper used
everywhere distance needs to be estimated without an API call.

Changing a weight or threshold here changes optimizer.py's behavior directly.
These aren't defaults with overrides per call, except for the five score
weights.
"""

from math import radians, sin, cos, sqrt, atan2, asin
from datetime import datetime, timezone

# SCORE WEIGHTS - must sum to 1.0
DEMAND_WEIGHT = 0.5
CHARGER_WEIGHT = 0.14
BATTERY_WEIGHT = 0.26
DEMAND_ANNUAL_WEIGHT = 0.1

# PENALTY WEIGHT
RELO_COST_WEIGHT = 0.25

#Minimum autonomy level for a vehicle to be considered for relocation.
MIN_AUTONOMY_LEVEL = 40
MIN_BATTERY_LEVEL_FOR_CHARGING = 80

# Scoring constants
AUTONOMY_SCORE_CAP_MILES = 250
# Average speed assumption for converting distance to drive time.
AVG_CITY_SPEED_MPH = 15
# Buffer time for relocation overhead in minutes: covers the Fleet Associate's
# time to park, check the vehicle, plug in/unplug a charger, etc.
RELOCATION_OVERHEAD_MINUTES = 5

# Relocation cost-in-dollars assumptions
FLEET_MEMBERS_PER_RELOCATION = 2
LABOR_RATE_PER_HOUR_PER_PERSON = (
    22.12  # 46k per year (hourly rate + 6k per year in benefits)
)
CHARGE_COST_PER_MILE = 0.10


# Sitting-time thresholds (hours since the vehicle last moved) that classify a move.
# Vehicles sitting less than RECOMMENDED_MOVE_SITTING_HOURS aren't shown as relocation
# candidates at all. Only recommended (16-72h) and required (72h+) moves are surfaced.
RECOMMENDED_MOVE_SITTING_HOURS = 16
REQUIRED_MOVE_SITTING_HOURS = 72

# Vehicles sitting in a low-demand neighborhood (capacity tier 1, see
# get_neighborhood_capacity) are also relocation candidates once they hit this
# much shorter threshold — they're unlikely to get picked up organically.
LOW_DEMAND_SITTING_HOURS = 4

# Demand score floor applied to a block when its neighborhood currently has
# zero parked vehicles, so a neighborhood that's genuinely empty isn't
# starved out by a low baked-in demand score. Only raises the score (never
# lowers it), and only when it's below the floor to begin with. Set just
# above the tier-3 threshold in get_neighborhood_capacity so an empty
# neighborhood is competitive without automatically outscoring real demand.
ZERO_VEHICLE_DEMAND_FLOOR = 45

# Hard cap on how far a vehicle can be relocated, checked against real routed
# distance (both the block-centroid estimate and the final vehicle-to-pod trip).
MAX_RELOCATION_RADIUS_MILES = 5


# Charger scoring thresholds (core_functions/scoring.py)
# A block is "near enough" to a charger to skip a dedicated charging trip if
# drive time is under this many minutes.
CHARGER_NEARBY_DRIVE_MINUTES = 3
# Charger score decays linearly from 100 to 0 as drive time goes from 0 to
# this many minutes.
CHARGER_SCORE_DECAY_MINUTES = 10
# How many of the closest (by straight-line distance) chargers to actually
# route to when picking the best one for a block.
CHARGER_HAVERSINE_PREFILTER_CANDIDATES = 3

# Cold-weather range model (core_functions/scoring.py)
# Coordinates used to fetch the current temperature for the autonomy score's
# cold-weather penalty.
TWIN_CITIES_LAT, TWIN_CITIES_LON = 44.98, -93.27
# Rough monthly average temps (°F) for MSP, used only if the live weather
# call fails.
MONTHLY_AVG_TEMP_F = {
    1: 15,
    2: 20,
    3: 33,
    4: 48,
    5: 60,
    6: 70,
    7: 75,
    8: 73,
    9: 63,
    10: 50,
    11: 34,
    12: 19,
}
# Manufacturer cold-weather range table, EPA-style 86°F baseline down to
# -40°F, in 15°F increments matching manufacturer test data.
COLD_WEATHER_RANGE_TABLE = {
    "Chevy Bolt": {
        86: 259,
        77: 259,
        68: 259,
        59: 250,
        50: 240,
        41: 230,
        32: 180,
        23: 170,
        14: 160,
        5: 150,
        -4: 140,
        -13: 135,
        -22: 130,
        -31: 130,
        -40: 125,
    },
    "Chevy Equinox": {
        86: 319,
        77: 319,
        68: 319,
        59: 310,
        50: 300,
        41: 280,
        32: 270,
        23: 250,
        14: 240,
        5: 220,
        -4: 200,
        -13: 180,
        -22: 170,
        -31: 160,
        -40: 150,
    },
    "Nissan Leaf Plus": {
        86: 226,
        77: 226,
        68: 226,
        59: 220,
        50: 210,
        41: 200,
        32: 180,
        23: 170,
        14: 160,
        5: 150,
        -4: 140,
        -13: 130,
        -22: 120,
        -31: 110,
        -40: 100,
    },
    "Nissan Leaf": {
        86: 150,
        77: 150,
        68: 150,
        59: 145,
        50: 140,
        41: 135,
        32: 130,
        23: 120,
        14: 110,
        5: 95,
        -4: 80,
        -13: 75,
        -22: 70,
        -31: 65,
        -40: 60,
    },
}


# Haversine Calculation
def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth radius in miles
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000  # Earth radius in meters
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return R * 2 * asin(sqrt(a))


def hours_sitting(vehicle):
    last_moving = vehicle.get("location", {}).get("lastMovingDate")
    if not last_moving:
        return 0.0
    try:
        last_moving_dt = datetime.fromisoformat(last_moving.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last_moving_dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return 0.0


def fleet_number(s, sep):
    """Leading fleet number off a vehicle's name/plate (e.g. "207 Chevy Equinox"
    with sep=" ", or "207 - 123456" with sep="-"), or None if the field is a
    placeholder/test row (e.g. plate "newnoplate") rather than a real vehicle.
    """
    try:
        return int(float(s.split(sep)[0].strip()))
    except (ValueError, AttributeError):
        return None
