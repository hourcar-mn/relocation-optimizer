"""Authenticates against Vulog's fleet manager API.

get_token and get_headers are the credentials plumbing consumed by
core_functions/data_fetcher.py's get_vehicles_cached to pull the live fleet
list. Nothing else in this module talks to Vulog directly.
"""

import requests
import os
from dotenv import load_dotenv

load_dotenv()

VULOG_SERVER = os.getenv("VULOG_SERVER")
FLEET_ID     = os.getenv("VULOG_FLEET_ID")
USERNAME     = os.getenv("API_USERNAME")
PASSWORD     = os.getenv("API_PASSWORD")
CLIENT_ID    = os.getenv("CLIENT_ID")
CLIENT_SECRET= os.getenv("CLIENT_SECRET")
API_KEY      = os.getenv("VULOG_API_KEY")


# ── STEP 1: GET TOKEN ─────────────────────────────────────────────────────────
def get_token():
    response = requests.post(
        f"{VULOG_SERVER}/auth/realms/{FLEET_ID}/protocol/openid-connect/token",
        data={
            "username":      USERNAME,
            "password":      PASSWORD,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type":    "password",
        }
    )
    if response.status_code != 200:
        raise Exception(f"Auth failed: {response.status_code} {response.text}")
    return response.json()["access_token"]


# ── STEP 2: HEADERS ───────────────────────────────────────────────────────────
def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "accept":        "application/json",
        "x-api-key":     API_KEY,
    }
