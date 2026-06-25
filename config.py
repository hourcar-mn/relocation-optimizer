from math import radians, sin, cos, sqrt, atan2
# SCORE WEIGHTS
DEMAND_WEIGHT        = 0.2
CHARGER_WEIGHT       = 0.15
RELO_COST_WEIGHT     = 0.15
BATTERY_WEIGHT       = 0.2
POD_VACANCY_WEIGHT   = 0.20
TRIP_RATE_WEIGHT     = 0.15
MICRO_WEIGHT         = 0.05


#Haversine Calculation
def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth radius in miles
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))