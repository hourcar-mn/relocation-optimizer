"""Checks refresh_data's SQL-to-CSV column mapping without touching the
database: scoring.py reads lat/lon, pod_generator reads StartLat/StartLong,
and a rename that drifts would only fail at runtime. Run: python tests/test_refresh_data.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa

import pandas as pd
import refresh_data


def test_column_mapping():
    for name, _query, rename in refresh_data.DATASETS:
        # a row shaped like the query's SELECT list
        columns = [c for c in rename] or ["StartLat", "StartLong"]
        df = pd.DataFrame([[0.0] * len(columns)], columns=columns).rename(columns=rename)
        if name == "member_addresses.csv":
            assert list(df.columns) == ["lat", "lon"], df.columns
        else:
            assert "StartLat" in df.columns and "StartLong" in df.columns


def test_missing_password_exits():
    import os

    os.environ.pop("DB_PASSWORD", None)
    try:
        refresh_data._conn_str()
    except SystemExit:
        return
    raise AssertionError("_conn_str should exit without DB_PASSWORD")


if __name__ == "__main__":
    test_column_mapping()
    test_missing_password_exits()
    print("ok")
