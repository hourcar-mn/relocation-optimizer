# Fleet Relocation Optimizer

MILP-based tool that figures out where to move Evie vehicles based on demand, battery, chargers, and microlocation data. Also has a frontend that shows assignments on a map.

## Setup

Need Python 3.11+ and Node for the frontend.

```sh
pip install -r requirements.txt
```

copy `.env.example` to `.env` and fill in the Vulog creds.

Backend:

```sh
cd apis
python api_server.py
```

runs on localhost:8000

Frontend:

```sh
cd frontend
npm install
npm run dev
```

runs on localhost:5173, both need to be running at same time

in VS Code there are tasks for both of these — "Setup Dev Environment" once, then "Run Dev Environment" to start them together.

the frontend talks to `/api/*`, which Vite proxies to localhost:8000 in dev and the Worker routes to the container in production — same app code either way.

## Deploying (Cloudflare)

The whole thing runs as one Worker: static frontend + `/api/*` routes, with the FastAPI backend in a Cloudflare Container and results cached in R2.

- **Worker** (`worker/index.ts`) serves `frontend/dist` and proxies `/api/*` to the container.
- **Container** (`Dockerfile`) runs the same `apis/api_server.py` as local dev.
- **R2** (`relocation-optimizer` bucket) holds the last assignment run (`assignments/latest.json`), the input CSVs (`data/`), and the routing/pod caches the container rebuilds (`cache/`).
- **Cron** runs the optimizer on a Central-time schedule — every 30 minutes from 06:00 to 18:30, then every 3 hours (19:00, 22:00, 01:00, 04:00) — and overwrites the cached run. Cron triggers are UTC-only, so the trigger fires every 30 minutes around the clock and `worker/schedule.ts` decides which ticks do the work; that keeps the schedule correct across DST. To change the hours, edit `shouldRun()` there and run `node worker/schedule.check.ts`. A page load serves that cached run instantly; if it's older than 35 minutes the stale copy is still returned and a refresh kicks off behind it. Hitting "Run optimizer" with dashboard weights always does a live run.

### Task feed for the demand dashboard

`GET /api/tasks` returns `{ generated_at, tasks }` — the same cached run reshaped
into the task objects `hourcar-mn/demand-dashboard` renders in its task list (see
`core_functions/tasks.py`; `python test_tasks.py` checks the mapping). It answers
from the cached run and never blocks on a live one — when nothing is cached yet it
returns `generated_at: null` with an empty list and starts a run in the background,
so a client polling every 30 seconds gets an answer immediately instead of timing
out, and can't queue up one run per poll.

Cloudflare Access fronts this hostname and its cookie is per-hostname, so the
dashboard's browser code can't fetch this cross-origin. The dashboard reaches it
over a Worker service binding instead — a subrequest that never goes through the
edge, so Access doesn't apply. Nothing to configure on this side; the binding is
declared in the dashboard's `wrangler.toml` as `service = "relocation-optimizer"`.

One-time setup (PowerShell, from the repo root):

```powershell
npm install
npx wrangler login
npx wrangler r2 bucket create relocation-optimizer

# input data files, same ones described below. --remote is required — without it
# these go to the local wrangler simulator in .wrangler/ instead of real R2.
npx wrangler r2 object put "relocation-optimizer/data/Trip_History(Jan2025_Jun26).csv" --file "data_files/Trip_History(Jan2025_Jun26).csv" --remote
npx wrangler r2 object put relocation-optimizer/data/member_addresses.csv --file data_files/member_addresses.csv --remote

# secrets: Vulog creds, then R2 creds for the container
# (create an R2 API token in the dashboard first — Manage R2 API Tokens)
foreach ($s in "VULOG_SERVER","VULOG_FLEET_ID","VULOG_API_KEY","API_USERNAME","API_PASSWORD","CLIENT_ID","CLIENT_SECRET","R2_ENDPOINT","R2_BUCKET","R2_ACCESS_KEY_ID","R2_SECRET_ACCESS_KEY") { npx wrangler secret put $s }
```

`wrangler secret put` prompts for each value and writes it straight to Cloudflare — the values never land in the repo. `R2_BUCKET` is `relocation-optimizer` and `R2_ENDPOINT` is `https://<account-id>.r2.cloudflarestorage.com`.

Then `npm run deploy` (builds the frontend, builds the image, deploys). Needs Docker running locally to build the container image.

### CI

`.github/workflows/deploy.yml` typechecks the Worker, runs the cron-schedule and R2-sync checks, and builds the frontend on every PR and push. Pushes to `main` then deploy; PRs additionally build the container image, which is what catches a broken Dockerfile before it reaches `main`.

Two repo secrets are needed (Settings → Secrets and variables → Actions):

- `CLOUDFLARE_API_TOKEN` — create with the **Edit Cloudflare Workers** template. If the deploy fails while pushing the container image, the token also needs the Containers/Cloudchamber edit permission.
- `CLOUDFLARE_ACCOUNT_ID`

PRs don't get their own deployed copy. Cloudflare doesn't generate preview URLs for Workers that implement a Durable Object, and the container class is one — so a real per-PR preview would mean deploying a second Worker with its own container instances and R2 bucket. Not worth it for an internal tool; the PR build is the safety net instead.

**Lock it down before sharing the URL:** the dashboard shows live vehicle locations and plates. Add a Cloudflare Access application (Zero Trust → Access → Applications) over the Worker's hostname with a policy limited to hourcar.org emails. Cron triggers aren't affected by Access.

Note: `wrangler dev` **cannot run the container on Windows** ("Local development with containers is currently not supported on Windows. You should use WSL instead."). So the Worker-to-container path can only be exercised by deploying. The normal `python api_server.py` + `npm run dev` loop is unaffected and remains the way to work on the optimizer and the UI. If you do set up WSL later, `wrangler dev` reads secrets from a `.dev.vars` file in the repo root — same keys as `.env`.

## Data files (not in repo)

these are too big / sensitive to commit, grab them from the shared drive and drop in `data_files/`. In production the container pulls the same two files from R2 on demand (`core_functions/r2_sync.py`) — when issue #7 moves this to SQL, drop them from `INPUT_FILES` there:

- `Trip_History(Jan2025_Jun26).csv` (used for pod drop-off locations, not trip totals — see below)
- `member_addresses.csv` (columns: lat, lon)

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

Your first run might take a long time.

## Known issues

- overpass API (amenity data) rate limits sometimes, falls back gracefully (will be switching API soon)
- travel time / charger scores cached to disk, delete the `.pkl` files in `data_files/` if something seems stale
