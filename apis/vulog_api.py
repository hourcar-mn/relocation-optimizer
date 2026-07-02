import requests
import os
from dotenv import load_dotenv
import time

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


# ── STEP 3: ENDPOINTS ─────────────────────────────────────────────────────────
def get_vehicles(token):
    url = f"{VULOG_SERVER}/boapi/proxy/fleetmanager/public/fleets/{FLEET_ID}/vehicles"
    response = requests.get(url, headers=get_headers(token))
    response.raise_for_status()
    return response.json()

def get_trips_history(token):
    url = f"{VULOG_SERVER}/boapi/proxy/trip/fleets/{FLEET_ID}/trips"
    response = requests.get(url, headers=get_headers(token))
    response.raise_for_status()
    return response.json()

def get_pois(token):
    url = f"{VULOG_SERVER}/boapi/proxy/geoloc/fleets/{FLEET_ID}/pois"
    response = requests.get(url, headers=get_headers(token))
    response.raise_for_status()
    return response.json()

def get_zones(token):
    url = f"{VULOG_SERVER}/boapi/proxy/geoloc/fleets/{FLEET_ID}/zones"
    response = requests.get(url, headers=get_headers(token))
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    token    = get_token()
    vehicles = get_vehicles(token)

    print(f"Total vehicles: {len(vehicles)}")
    for v in vehicles[:3]:
        print(f"  {v['name']} — status: {v['vehicle_status']}")