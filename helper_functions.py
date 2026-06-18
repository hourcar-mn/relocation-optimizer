# Import necessary packages
import requests
import os
import time
import pytz
import pyodbc
import re
import geopandas as gpd
import pandas as pd
from uuid import UUID
from ratelimit import limits
from datetime import datetime, timedelta
from urllib.parse import quote
from pytictoc import TicToc
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

# Get the environment variables
USERNAME = os.getenv('API_USERNAME')
PASSWORD = os.getenv('API_PASSWORD')
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')

# API keys and sensitive info
VULOG_API_KEY = os.getenv('VULOG_API_KEY')
LYTX_API_KEY = os.getenv('LYTX_API_KEY')
DB_PASSWORD = os.getenv('DB_PASSWORD')
GEOAPIFY_API_KEY = os.getenv('GEOAPIFY_API_KEY')

# Define the connection string for the database
conn_str = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=tcp:REDACTED_DB_SERVER,1433;"
    "Database=REDACTED_DB_NAME;"
    "Uid=REDACTED_DB_USER;"
    f"Pwd={DB_PASSWORD};"
    "Encrypt=yes;"
    "TrustServerCertificate=no;"
    "Connection Timeout=120;"
)

# Create a session for making HTTP requests
session = requests.Session()


class TokenManager:
    def __init__(self):
        self.access_token = None  # Store the access token
        self.token_expiration_time = None  # Store the token expiration time

    def authenticate(self):
        """
        Authenticate and get a new access token.
        Raises an exception if authentication fails or the access token is not found in the response.
        """
        response = session.post(
            "https://aima-us.vulog.net/auth/realms/HOURCAR-USMSP/protocol/openid-connect/token",
            data={
                "username": USERNAME,
                "client_secret": CLIENT_SECRET,
                "grant_type": "password",
                "client_id": CLIENT_ID,
                "password": PASSWORD,
            },
        )

        # Check if the request was successful
        if response.status_code != 200:
            raise Exception(
                f"Error getting access token: {response.status_code}")

        # Parse the response to get the access token
        response_json = response.json()
        access_token = response_json.get("access_token")

        # Check if the access token is present in the response
        if not access_token:
            raise Exception("Access token not found in the response")

        self.access_token = access_token
        # Assuming token is valid for 3600 seconds for simplicity
        self.token_expiration_time = time.time() + 3600

    def refresh_token(self):
        """
        Refresh the access token by authenticating again.
        """
        print("Refreshing token...")
        self.access_token = self.authenticate()
        self.token_expiration_time = time.time() + 3600

    def get_access_token(self):
        """
        Get the access token, refreshing it if necessary.
        Returns the access token.
        """
        if self.access_token is None or time.time() > self.token_expiration_time:
            self.authenticate()
        return self.access_token


# Instantiate the TokenManager class
token_manager = TokenManager()

# Get headers for Vulog APIs


def get_headers(refresh=False, accept_json_only=False):
    """
    Get the headers for the requests.
    If refresh is True, refresh the access token.
    Returns a dictionary with the headers.
    """
    if refresh:
        token_manager.refresh_token()

    if accept_json_only:
        accepts_type = 'application/json'
    else:
        accepts_type = 'application/vnd.geo+json'

    headers = {
        'Authorization': f'Bearer {token_manager.get_access_token()}',
        'accept': accepts_type,
        'x-api-key': VULOG_API_KEY
    }

    return headers


@limits(calls=18, period=1)
def get_insert_rows(table, conn, cursor, insert_function=None, size=200, is_paginated=False, startDate=None, endDate=None):
    """
    Fetch rows from the API and optionally insert them into a database.
    Args:
        table (str): The table name (e.g., "pois", "vehicles").
        insert_function (function, optional): The function to insert rows into the database.
        size (int, optional): The number of rows per page.
        is_paginated (bool, optional): Whether the API response is paginated.
        startDate (str, optional): The start date for fetching rows.
        endDate (str, optional): The end date for fetching rows.
    """
    # Create a TicToc object to time the code execution
    t = TicToc()
    t.tic()

    # Define the base URLs for different tables
    table_dict = {
        "pois": {"base_url": "https://aima-us.vulog.net/boapi/proxy/geoloc/fleets/HOURCAR-USMSP/pois"},
        "vehicles": {"base_url": "https://aima-us.vulog.net/boapi/proxy/fleetmanager/public/fleets/HOURCAR-USMSP/vehicles"},
        "trips": {"base_url": "https://aima-us.vulog.net/boapi/proxy/trip/fleets/HOURCAR-USMSP/trips"},
        "users": {"base_url": "https://aima-us.vulog.net/boapi/proxy/user/fleets/HOURCAR-USMSP/users"},
        "scheduled_maintenance": {"base_url": "https://aima-us.vulog.net/boapi/proxy/user/fleets/HOURCAR-USMSP/scheduledRules/unavailability/vehicles"},
        "zones": {"base_url": "https://aima-us.vulog.net/boapi/proxy/geoloc/fleets/HOURCAR-USMSP/zones"},
        "stations": {"base_url": "https://aima-us.vulog.net/boapi/proxy/user/scheduledBooking/fleets/HOURCAR-USMSP/stations"}
    }

    # Create base URL using table dictionary
    base_url = table_dict[table]["base_url"]

    # For paginated endpoints
    if is_paginated:
        all_rows = []
        page = 0

        print(f"Fetching rows for {table} table.")

        while True:
            if startDate is None:
                url = f"{base_url}?page={page}&size={size}"
            else:
                startDateFormatted = format_date(startDate)
                endDateFormatted = format_date(endDate)
                if table == "scheduled_maintenance":
                    url = f"{base_url}?from={startDateFormatted}&to={endDateFormatted}&page={page}&size={size}"
                else:
                    url = f"{base_url}?page={page}&size={size}&startDate={startDateFormatted}&endDate={endDateFormatted}"

            # Get API response
            response = session.get(url, headers=get_headers())

            # Unauthorized; Token might have expired
            if response.status_code == 401:
                print("Token may have expired. Trying to refresh...")
                response = session.get(url, headers=get_headers(refresh=True))
                print("Token refreshed")

            # Get rows if API response is valid
            if response.status_code == 200:
                selected_rows = response.json()
                all_rows.extend(selected_rows)

                if len(selected_rows) < size:
                    print(f"Fetched the last page for {table} table.")
                    break

                page += 1

            # Raise error for rate limiting
            elif response.status_code == 429:
                raise ValueError(
                    f"Encountered error 429: {response.text}")

            else:
                print(f'Error: {response.status_code} - {response.reason}')

        print(f"Total rows fetched for {table} table: {len(all_rows)}")

        # If an insert function is given, then insert all rows to database
        if insert_function:
            insert_function(all_rows, conn, cursor)

        t.toc('Fetching rows took')

    # For non-paginated endpoints
    else:
        while True:
            url = f"{base_url}"
            response = session.get(url, headers=get_headers())

            if response.status_code == 401:  # Unauthorized; Token might have expired
                print("Token may have expired. Trying to refresh...")
                response = session.get(url, headers=get_headers(refresh=True))
                print("Token refreshed")

            if response.status_code == 200:
                all_rows = response.json()

                insert_function(all_rows, conn, cursor)

                t.toc('Fetching rows took')
                break

            # Raise value error if rate limit is hit
            elif response.status_code == 429:
                raise ValueError(
                    f"Encountered error 429: {response.text}")
            else:
                print(f'Error: {response.status_code} - {response.reason}')
                break


def fetch_rows(table=None, query=None):
    """
    Fetches rows from a database table or executes a custom SQL query.

    Parameters:
    - table (str, optional): The table name ('zones', 'pois', 'vehicles') to run a predefined query.
    - query (str, optional): A custom SQL query to execute. Ignored if 'table' is specified.

    Returns:
    - list of pyodbc.Row: Fetched rows from the database.
    """

    if table:
        table_query_dict = {
            "zones": """SELECT ZoneID, ZoneName, Type, Coordinates FROM dbo.Zones;""",
            "pois": """SELECT PoiName, Type, ZoneID, Longitude, Latitude, ZoneName FROM dbo.PointsOfInterest;""",
            "vehicles": """SELECT VehicleID, VehicleName FROM dbo.Vehicles;"""
        }

        query = table_query_dict[table]

    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_vehicle_name(vehicle_id, vehicles):
    """
    Return the name of the vehicle based on the given vehicle_id.
    Args:
        vehicle_id (int): The ID of the vehicle.
    Returns:
        str: The name of the vehicle, or None if not found.
    """
    vehicle_name = None

    for vehicle in vehicles:
        if vehicle.VehicleID == vehicle_id:
            vehicle_name = vehicle.VehicleName
            break

    return vehicle_name


def get_zone_info():
    """
    Get detailed information for zones, including nicer labels for POIs and tax zones.
    Returns:
        dict: A dictionary with zone information.
    """
    zone_labels = {}

    zones = fetch_rows("zones")
    pois = fetch_rows("pois")

    # Transform POIs into the same format as zones
    poi_names = {}
    for poi in pois:
        zone_id = poi[2]
        poi_names[zone_id] = {
            'Name': poi[0],  # POI Name
            'Type': poi[1]   # POI Type
        }

    tax_zone_mapping = {
        "Tax 988": "Saint Paul",
        "Tax 903": "Minneapolis",
        "Tax 8375": "Little Canada, Maplewood",
        "Tax 8525": "St. Louis Park, Richfield, Bloomington"
    }

    for zone in zones:
        zone_id = zone[0]
        zone_name = zone[1]
        zone_type = zone[2]

        if zone_id in poi_names:
            zone_labels[zone_id] = poi_names[zone_id]
        elif zone_type == 'tax':
            if zone_name in tax_zone_mapping:
                zone_labels[zone_id] = {
                    'Name': tax_zone_mapping[zone_name],
                    'Type': zone_type
                }
            else:
                zone_labels[zone_id] = {
                    'Name': zone_name,
                    'Type': zone_type
                }
        else:
            zone_labels[zone_id] = {
                'Name': zone_name,
                'Type': zone_type
            }

    return zone_labels


def extract_first_number(name):
    """
    Extract the first number from the vehicle name.
    Args:
        name (str): The vehicle name.
    Returns:
        int: The first number found in the name, or None if no number is found.
    """
    if name:
        # Find all sequences of digits in the string
        matches = re.findall(r'\d+', name)
        if matches:
            # Return the first match as an integer
            return int(matches[0])
    # Default value if no number is found
    return None


def format_date(date_str):
    """
    Format the date string to the expected format.
    Args:
        date_str (str): The date string to format.
    Returns:
        str: The formatted date string.
    Raises:
        ValueError: If the date string is not in a recognized format.
    """
    # Define the expected format
    expected_format = "%Y-%m-%dT%H%%3A%M%%3A%SZ"

    try:
        # Parse the date string to a datetime object
        dt = datetime.strptime(date_str, expected_format)
        return date_str  # If it matches the format, return it as is
    except ValueError:
        pass  # If parsing fails, continue to formatting

    # Try to parse the date string in a more general format
    try:
        dt = datetime.fromisoformat(date_str.replace('%3A', ':').rstrip('Z'))
        # Format it in the expected format and return
        formatted_date = dt.strftime("%Y-%m-%dT%H:%M:%S")
        return quote(formatted_date) + 'Z'
    except ValueError:
        raise ValueError("The date string is not in a recognized format")


def pull_dates(date, year=None):
    """
    Pull the desired date based on the provided date string.
    Args:
        date (str): The date string (e.g., "today", "seven_days_ago").
        year (int, optional): The year for "first_day_of" or "last_day_of".
    Returns:
        str: The formatted date string.
    Raises:
        Exception: If the date string is not recognized.
    """
    today = datetime.now(pytz.timezone('America/Chicago')).astimezone(pytz.utc)
    if date == "today":
        today = today.strftime("%Y-%m-%dT%H%%3A%M%%3A%S.000Z")
        return today
    elif date == "yesterday":
        yesterday = (today - timedelta(days=1)).astimezone(pytz.utc)
        yesterday = yesterday.strftime("%Y-%m-%dT%H:%M:%S")
        return yesterday
    elif date == "tomorrow":
        tomorrow = (today + timedelta(days=1)).astimezone(pytz.utc)
        tomorrow = tomorrow.strftime("%Y-%m-%dT%H:%M:%S")
        return tomorrow
    elif date == "seven_days_ago":
        seven_days_ago = (today - timedelta(days=7)).astimezone(pytz.utc)
        seven_days_ago = seven_days_ago.strftime("%Y-%m-%dT%H:%M:%S")
        return seven_days_ago
    elif date == "three_days_ago":
        three_days_ago = (today - timedelta(days=3)).astimezone(pytz.utc)
        three_days_ago = three_days_ago.strftime("%Y-%m-%dT%H:%M:%S")
        return three_days_ago
    elif date == "twentynine_days_ahead":
        twentynine_days_ahead = (
            today + timedelta(days=29)).astimezone(pytz.utc)
        twentynine_days_ahead = twentynine_days_ahead.strftime(
            "%Y-%m-%dT%H%%3A%M%%3A%S.000Z")
        return twentynine_days_ahead
    elif "first_day_of" in date:
        desired_date = datetime(
            year, 1, 1, 0, 0, 0).strftime("%Y-%m-%dT%H:%M:%S")
        return desired_date
    elif "last_day_of" in date:
        desired_date = datetime(year, 12, 31, 23, 59,
                                0).strftime("%Y-%m-%dT%H:%M:%S")
        return desired_date
    else:
        raise Exception("Date not found")


def convert_to_guid(trip_id):
    """
    Convert a trip ID to a GUID.
    Args:
        trip_id (str): The trip ID.
    Returns:
        UUID: The converted GUID, or None if the conversion fails.
    """
    if trip_id and len(trip_id) == 32 and re.match(r'^[A-Fa-f0-9]+$', trip_id):
        formatted_trip_id = (
            trip_id[:8] + '-' +
            trip_id[8:12] + '-' +
            trip_id[12:16] + '-' +
            trip_id[16:20] + '-' +
            trip_id[20:]
        )
        try:
            return UUID(formatted_trip_id)
        except ValueError:
            return None
    return None


def load_neighborhood_shapefiles():
    # Load neighborhood shapefiles
    mpls_neighborhood_gdf = gpd.read_file(
        "helper_files/mpls_neighborhoods/Minneapolis_Neighborhoods.shp"
    )

    stpaul_neighborhood_gdf = gpd.read_file(
        "helper_files/stpaul_neighborhoods/District_Council.shp"
    )

    # If the CRS is already set, and it's different from EPSG:4326, transform it
    if mpls_neighborhood_gdf.crs and mpls_neighborhood_gdf.crs != "EPSG:4326":
        mpls_neighborhood_gdf = mpls_neighborhood_gdf.to_crs(epsg=4326)
    else:
        # Set the CRS if it's not already set
        mpls_neighborhood_gdf.set_crs(epsg=4326, inplace=True)

    if stpaul_neighborhood_gdf.crs and stpaul_neighborhood_gdf.crs != "EPSG:4326":
        stpaul_neighborhood_gdf = stpaul_neighborhood_gdf.to_crs(epsg=4326)
    else:
        stpaul_neighborhood_gdf.set_crs(epsg=4326, inplace=True)

    # Ensure columns match by renaming if necessary
    stpaul_neighborhood_gdf = stpaul_neighborhood_gdf.rename(
        columns={"planning_1": "BDNAME"}
    )

    # Combine the Minneapolis and Saint Paul neighborhood GeoDataFrames
    combined_neighborhood_gdf = pd.concat(
        [mpls_neighborhood_gdf, stpaul_neighborhood_gdf]
    )

    return combined_neighborhood_gdf


def load_and_prepare_geocoding_shapefiles():
    # Load shapefiles
    minneapolis_gdf = gpd.read_file(
        'helper_files/mpls_neighborhoods/Minneapolis_Neighborhoods.shp')
    saint_paul_gdf = gpd.read_file(
        'helper_files/stpaul_neighborhoods/District_Council.shp')
    metro_gdf = gpd.read_file(
        'helper_files/metro_counties/Counties.shp')
    minnesota_gdf = gpd.read_file(
        'helper_files/minnesota_boundary/Boundaries_of_Minnesota.shp')
    usa_gdf = gpd.read_file(
        'helper_files/state_boundaries/tl_2023_us_state.shp')

    # Ensure all GeoDataFrames are in EPSG:4326
    crs_epsg = "EPSG:4326"

    if minneapolis_gdf.crs != crs_epsg:
        minneapolis_gdf = minneapolis_gdf.to_crs(crs_epsg)
    if saint_paul_gdf.crs != crs_epsg:
        saint_paul_gdf = saint_paul_gdf.to_crs(crs_epsg)
    if metro_gdf.crs != crs_epsg:
        metro_gdf = metro_gdf.to_crs(crs_epsg)
    if minnesota_gdf.crs != crs_epsg:
        minnesota_gdf = minnesota_gdf.to_crs(crs_epsg)
    if usa_gdf.crs != crs_epsg:
        usa_gdf = usa_gdf.to_crs(crs_epsg)

    return minneapolis_gdf, saint_paul_gdf, metro_gdf, minnesota_gdf, usa_gdf
