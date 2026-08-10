from math import radians, sin, cos, sqrt, atan2

# SCORE WEIGHTS - must sum to 1.0
DEMAND_WEIGHT = 0.5
CHARGER_WEIGHT = 0.14
BATTERY_WEIGHT = 0.26
TRIP_RATE_WEIGHT = 0.1

# PENALTY WEIGHT
RELO_COST_WEIGHT = 0.25

#Minimum autonomy level for a vehicle to be considered for relocation.
MIN_AUTONOMY_LEVEL = 40
MIN_BATTERY_LEVEL_FOR_CHARGING = 80

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

# Relocation cost-in-dollars assumptions
FLEET_MEMBERS_PER_RELOCATION = 2
LABOR_RATE_PER_HOUR_PER_PERSON = 22.12 # 46k per year (hourly rate + 6k per year in benefits)
CHARGE_COST_PER_MILE = 0.10 



# Sitting-time thresholds (hours since the vehicle last moved) that classify a move.
# Vehicles sitting less than SOFT_MOVE_SITTING_HOURS aren't shown as relocation
# candidates at all — only soft (24-72h) and hard (72h+) moves are recommended.
SOFT_MOVE_SITTING_HOURS = 16
HARD_MOVE_SITTING_HOURS = 72


# Haversine Calculation
def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth radius in miles
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))
