"""Sync data files and API caches between local disk and R2.

Container disk is ephemeral — it resets whenever the container stops — so the
inputs that aren't in git (the trip history / member address CSVs) get pulled
down before a run, and the expensive-to-rebuild caches (travel time matrix,
pods, micro scores, charger scores) get pushed back up after one.

Every function here no-ops when the R2_* env vars are unset, so local
development reads and writes data_files/ exactly as it always has.

When issue #7 lands and the CSVs come from SQL instead, delete the entries from
INPUT_FILES — the cache sync is independent of where the inputs come from.
"""

import os
import shutil
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data_files"
CACHE_DIR = DATA_DIR / "_api_cache"

# Inputs not in git, pulled from R2 when missing locally. See README.
INPUT_FILES = ["member_addresses.csv"]

# 200MB, and only read when the pod cache needs rebuilding (once a day), so it's
# fetched on demand via ensure_input() instead of on every run.
TRIP_HISTORY_CSV = "Trip_History(Jan2025_Jun26).csv"

# Caches that live outside _api_cache/
EXTRA_CACHE_FILES = ["_pod_route_cache.pkl"]

INPUT_PREFIX = "data/"
CACHE_PREFIX = "cache/"


def _client():
    """boto3 S3 client pointed at R2, or None when R2 isn't configured."""
    endpoint = os.getenv("R2_ENDPOINT")
    bucket = os.getenv("R2_BUCKET")
    if not endpoint or not bucket:
        return None, None

    import boto3  # imported lazily so local runs don't need it installed

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )
    return client, bucket


def _download(client, bucket, key, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    obj = client.get_object(Bucket=bucket, Key=key)
    # streamed, not read() into memory — the trip history CSV is 200MB
    with open(dest, "wb") as f:
        shutil.copyfileobj(obj["Body"], f)
    # preserve the upload time as the file's mtime — every cache in this
    # codebase decides freshness from mtime, so resetting it to "now" on
    # download would make a stale cache look fresh.
    mtime = obj["LastModified"].timestamp()
    os.utime(dest, (mtime, mtime))


def is_configured():
    """True when R2 is wired up, i.e. we're running in the container."""
    return bool(os.getenv("R2_ENDPOINT") and os.getenv("R2_BUCKET"))


def ensure_input(name):
    """Fetch one input file from R2 if it isn't on disk yet. No-op locally."""
    dest = DATA_DIR / name
    if dest.exists():
        return dest
    client, bucket = _client()
    if client is None:
        return dest
    print(f"[r2] pulling input {name} on demand...")
    _download(client, bucket, INPUT_PREFIX + name, dest)
    return dest


def pull_from_r2():
    """Fetch missing input files and any cached artifacts from R2."""
    client, bucket = _client()
    if client is None:
        return

    for name in INPUT_FILES:
        dest = DATA_DIR / name
        if dest.exists():
            continue
        try:
            _download(client, bucket, INPUT_PREFIX + name, dest)
            print(f"[r2] pulled input {name}")
        except Exception as e:
            print(f"[r2] could not pull input {name}: {e}")

    try:
        listing = client.list_objects_v2(Bucket=bucket, Prefix=CACHE_PREFIX)
    except Exception as e:
        print(f"[r2] could not list caches: {e}")
        return

    for obj in listing.get("Contents", []):
        name = obj["Key"][len(CACHE_PREFIX):]
        if not name:
            continue
        dest = DATA_DIR / name if name in EXTRA_CACHE_FILES else CACHE_DIR / name
        # local copy is at least as new as R2's — keep it
        if dest.exists() and dest.stat().st_mtime >= obj["LastModified"].timestamp():
            continue
        try:
            _download(client, bucket, obj["Key"], dest)
            print(f"[r2] pulled cache {name}")
        except Exception as e:
            print(f"[r2] could not pull cache {name}: {e}")


def push_to_r2():
    """Upload the caches this run built so the next container start reuses them."""
    client, bucket = _client()
    if client is None:
        return

    paths = list(CACHE_DIR.glob("*")) + [DATA_DIR / n for n in EXTRA_CACHE_FILES]
    for path in paths:
        if not path.is_file():
            continue
        try:
            client.put_object(
                Bucket=bucket, Key=CACHE_PREFIX + path.name, Body=path.read_bytes()
            )
        except Exception as e:
            print(f"[r2] could not push cache {path.name}: {e}")
