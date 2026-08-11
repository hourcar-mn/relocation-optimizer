# Fleet Relocation Optimizer

MILP-based tool that figures out where to move Evie vehicles based on demand, battery, chargers, and microlocation data. Also has a frontend that shows assignments on a map.

## Setup

Need Python 3.11+ and Node for the frontend.

pip install -r requirements.txt


Backend:

cd apis
python api_server.py

runs on localhost:8000

Frontend:

cd frontend
npm install
npm run dev

runs on localhost:5173, both need to be running at same time

## Data files (not in repo)

these are too big / sensitive to commit, grab them from the shared drive and drop in `data_files/`:

- `Trip_History(Jan2025_Jun26).csv` (used for pod drop-off locations, not trip totals — see below)
- `member_addresses.csv` (columns: lat, lon)
- `block_groups_with_demand (8_10).geojson` — primary block groups file (demand + trip totals, both broken out by day type/season/6-hour time window)

neighborhoods geojson should already be in there.

ping me if you need drive access

## How it works (roughly)

1. pulls live vehicle + charger data from Vulog/Evie, filters to fleet vehicles actually eligible for relocation (excludes totalled, offboarding, currently booked, etc.)
2. loads block group demand data (baked-in demand percentiles + member address density + historical trip counts, all read straight off the demand geojson) and computes a per-block-group demand score
3. scores every vehicle-block pair on five factors: demand, charger proximity (real driving time, not straight-line), battery/autonomy, historical trip rate, and relocation cost (drive time penalty)
4. feeds the score matrix into a MILP solver, which picks the best set of assignments subject to constraints — one relocation per vehicle, capacity per block, neighborhood-level caps by demand tier, charger plug availability for vehicles that need charging
5. for each assignment, picks a specific pod (exact drop-off spot) within the block using trip-start clustering + nearby amenities
6. routes vehicle -> pod to get real drive time/distance, and returns the full assignment list (score breakdown, battery, charger status, drop-off location, etc.) to the frontend for the map + assignment cards

charger scores, vehicle travel times, and pod routes are all cached to disk since routing calls are the slow part — repeat runs mostly reuse what hasn't changed instead of re-hitting the routing API every time. 

historical trip counts (the "trip rate" score factor) come from the `Total trips: Summarized` column baked into the demand geojson, not a live spatial join against the trip history CSV. The CSV is still used for one thing: clustering individual trip-start coordinates into candidate pod (drop-off) locations, since the geojson only has per-block aggregates, not individual points.
- Your first run might take a long time.

## Known issues

- overpass API (amenity data) rate limits sometimes, falls back gracefully (will be switching API soon)
- travel time / charger scores cached to disk, delete the `.pkl` files in `data_files/` if something seems stale