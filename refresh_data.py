"""Monthly refresh of the two SQL-sourced input files.

The optimizer never talks to SQL — it reads these CSVs from R2 (see
core_functions/r2_sync.py). This script is the only thing that queries the
database, so it has to run somewhere Azure SQL's firewall allows (the office
network today, see issue #8), on a schedule. Everything downstream is unchanged.

    python refresh_data.py

Needs DB_PASSWORD in .env, the R2_* vars to upload, and `pip install pyodbc`
plus the Microsoft ODBC Driver 18 for SQL Server. pyodbc is deliberately not in
requirements.txt — the container doesn't need it and can't build it.
"""

import os
import sys
import pandas as pd
from dotenv import load_dotenv
from core_functions import r2_sync

# (filename, query, {sql column: csv column})
DATASETS = [
    (
        "trip_history.csv",
        """
        SELECT TripID, StartLat, StartLong, EndLat, EndLong
        FROM Trips
        WHERE IsCancel = 0
          AND ServiceType = 'FREE_FLOATING';
        """,
        {},
    ),
    (
        # lat/lon are what core_functions/scoring.py expects
        "member_addresses.csv",
        """
        SELECT AddressLat, AddressLong
        FROM Users
        WHERE AccountStatus != 'ARCHIVED'
          AND AddressLat != -1
          AND AddressLabel IN ('Minneapolis', 'Saint Paul');
        """,
        {"AddressLat": "lat", "AddressLong": "lon"},
    ),
]


def _conn_str():
    keys = ("DB_SERVER", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        sys.exit(f"{', '.join(missing)} not set — see .env.example")
    server, name, user, password = (os.environ[k] for k in keys)
    return (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;"
        f"Database={name};"
        f"Uid={user};"
        f"Pwd={password};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )


def fetch(query):
    import pyodbc  # only this script needs it, keep the import off every other run

    with pyodbc.connect(_conn_str()) as conn:
        return pd.read_sql(query, conn)


def main():
    load_dotenv()
    for name, query, rename in DATASETS:
        df = fetch(query).rename(columns=rename)
        # an empty result means the query or the schema broke, not that hourcar
        # has no members — writing it out would blank the copy in R2
        if df.empty:
            sys.exit(f"{name}: query returned 0 rows, refusing to overwrite")

        path = r2_sync.DATA_DIR / name
        df.to_csv(path, index=False)
        print(f"[refresh] {name}: {len(df)} rows -> {path}")
        r2_sync.upload_input(path)


if __name__ == "__main__":
    main()
