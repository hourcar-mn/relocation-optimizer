# Relocation Optimizer

Decides which Evie carshare vehicles to move and where to put them, based on demand, battery, charger availability and nearby amenities. Comes with a map frontend that shows each assignment.

Originally built by Kaleb Gezahegn at HOURCAR, with Noah Velick and Gunnar Aas.

## How it works

1. pulls live vehicle and charger data from Vulog/Evie and keeps the vehicles that can actually be moved
2. scores every vehicle and block group pair on demand, charger proximity, battery, trip history and drive time
3. a MILP solver (PuLP) picks the best set of moves: one per vehicle, capacity limits per block and neighborhood, chargers for low batteries
4. picks an exact drop-off spot inside each block from trip-start clusters and nearby amenities
5. routes each vehicle to its spot and sends the list to the frontend

routing calls are the slow part, so travel times, charger scores and pods are cached to disk. the first run takes a while.

## Running locally

needs Python 3.11+ and Node.

```sh
pip install -r requirements.txt
cp .env.example .env   # fill in the Vulog credentials
python apis/api_server.py
```

```sh
cd frontend
npm install
npm run dev
```

backend runs on localhost:8000, frontend on localhost:5173. the map needs a Mapbox public token in `frontend/.env.local` as `VITE_MAPBOX_TOKEN`.

the demand and neighborhood geojsons are in `data_files/`. trip history and member location CSVs aren't in the repo; `refresh_data.py` builds them from the database and uploads them to R2.

## Deploying

it runs as a Cloudflare Worker (`worker/index.ts`) that serves the frontend and forwards `/api/*` to the FastAPI backend in a Cloudflare Container. R2 holds the input CSVs, caches, and the last run. `npm run deploy` builds and deploys; secrets go in with `wrangler secret put` (see `.env.example` for the list). The GitHub workflows run the tests on every PR and deploy from `main`.

## Tests

```sh
for f in tests/test_*.py; do python "$f"; done
```

## License

MIT
