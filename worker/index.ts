import { Container } from "@cloudflare/containers";

export interface Env {
  OPTIMIZER: DurableObjectNamespace<OptimizerContainer>;
  RESULTS: R2Bucket;

  VULOG_SERVER: string;
  VULOG_FLEET_ID: string;
  VULOG_API_KEY: string;
  API_USERNAME: string;
  API_PASSWORD: string;
  CLIENT_ID: string;
  CLIENT_SECRET: string;

  R2_ENDPOINT: string;
  R2_BUCKET: string;
  R2_ACCESS_KEY_ID: string;
  R2_SECRET_ACCESS_KEY: string;
}

/** The FastAPI backend (apis/api_server.py), one shared instance. */
export class OptimizerContainer extends Container<Env> {
  defaultPort = 8000;
  // long enough to stay warm through a full run and a few button presses, short
  // enough not to bill for an idle container. Caches survive a stop via R2.
  sleepAfter = "10m";
  enableInternet = true;
  pingEndpoint = "/health";

  envVars = {
    VULOG_SERVER: this.env.VULOG_SERVER,
    VULOG_FLEET_ID: this.env.VULOG_FLEET_ID,
    VULOG_API_KEY: this.env.VULOG_API_KEY,
    API_USERNAME: this.env.API_USERNAME,
    API_PASSWORD: this.env.API_PASSWORD,
    CLIENT_ID: this.env.CLIENT_ID,
    CLIENT_SECRET: this.env.CLIENT_SECRET,
    R2_ENDPOINT: this.env.R2_ENDPOINT,
    R2_BUCKET: this.env.R2_BUCKET,
    R2_ACCESS_KEY_ID: this.env.R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY: this.env.R2_SECRET_ACCESS_KEY,
  };
}

const CACHE_KEY = "assignments/latest.json";

type CachedRun = {
  generated_at: string;
  params: string;
  assignments: unknown[];
  /** same run reshaped for the demand-dashboard task list; see core_functions/tasks.py */
  tasks?: unknown[];
};

function backend(env: Env) {
  return env.OPTIMIZER.getByName("optimizer");
}

/** Run the optimizer in the container and cache the result in R2. */
async function runOptimizer(env: Env, search: string): Promise<CachedRun> {
  const res = await backend(env).fetch(`http://container/assignments${search}`);
  if (!res.ok) {
    throw new Error(`optimizer run failed: ${res.status} ${await res.text()}`);
  }
  const payload: CachedRun = { ...(await res.json<CachedRun>()), params: search };
  await env.RESULTS.put(CACHE_KEY, JSON.stringify(payload));
  return payload;
}

const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { "content-type": "application/json" },
  });

/**
 * The last run, however old. Runs only happen when someone hits "Run Optimizer"
 * (which always sends weights, so it takes the live branch); nothing here
 * starts one on its own, so an idle day costs no container time.
 */
async function latestRun(env: Env, url: URL): Promise<CachedRun | null> {
  // custom weights / sitting hours from the dashboard, always a live run
  if (url.search) return runOptimizer(env, url.search);
  const cached = await env.RESULTS.get(CACHE_KEY);
  return cached ? cached.json<CachedRun>() : null;
}

/**
 * The demand-dashboard task list. Reached over a Worker service binding from
 * hourcar-demand-dashboard, not from a browser, since Cloudflare Access sits
 * in front of this hostname and its cookie doesn't cross origins.
 *
 * Serves whatever the last button-triggered run left; an empty list means no
 * run is cached yet (or the cached one predates the `tasks` field).
 */
async function tasks(env: Env, url: URL) {
  const payload = await latestRun(env, url);
  return json({
    generated_at: payload?.generated_at ?? null,
    tasks: payload?.tasks ?? [],
  });
}

export default {
  async fetch(request: Request, env: Env) {
    const url = new URL(request.url);
    // only /api/* reaches the Worker (see run_worker_first in wrangler.jsonc)
    const path = url.pathname.replace(/^\/api/, "");

    if (path === "/assignments") return json(await latestRun(env, url));
    if (path === "/tasks") return tasks(env, url);

    // everything else (/vehicles, /chargers, /weather, ...) is a cheap
    // passthrough to the same FastAPI app
    return backend(env).fetch(
      new Request(`http://container${path}${url.search}`, request)
    );
  },
};
