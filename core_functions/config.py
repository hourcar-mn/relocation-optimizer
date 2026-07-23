from math import radians, sin, cos, sqrt, atan2

# SCORE WEIGHTS - must sum to 1.0
DEMAND_WEIGHT = 0.32
CHARGER_WEIGHT = 0.32
BATTERY_WEIGHT = 0.26
TRIP_RATE_WEIGHT = 0.1

# PENALTY WEIGHT
RELO_COST_WEIGHT = 0.25

# Scoring constants
AUTONOMY_SCORE_CAP_MILES = 250
COLD_WEATHER_RANGE_FACTOR = 0.7
MIN_RELOCATION_MILES = 0.5
# Average speed assumption for converting distance to drive time.
AVG_CITY_SPEED_MPH = 15
# Buffer time for relocation overhead in minutes: covers the Fleet Associate's
# time to park, check the vehicle, plug in/unplug a charger, etc.
RELOCATION_OVERHEAD_MINUTES = 5
# OOS reasons to include when filtering vehicles for relocation.
INCLUDE_OOS = ["low-autonomy-level", "is-charging", "Towed vehicle", "Fleet Use"]


# Haversine Calculation
def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth radius in miles
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
