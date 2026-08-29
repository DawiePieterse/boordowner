#!/usr/bin/env python3
"""Backfill of hourly weather history from Open-Meteo's historical-forecast
API into the WeatherHistory table, for correlating conditions with harvest
data (2020 onward) on the admin/owner Weather tab.

Unlike the harvest import this isn't a true one-off: re-run it periodically
to pick up the days since the last import - it's safe to re-run, since it
replaces this script's own date range (HISTORY_START_DATE onward) each
time rather than appending. (The app itself also keeps the table current
on every Weather tab open - see weather.sync_recent_weather() - this
script is for a full rebuild.)

Only ever deletes/reinserts HISTORY_START_DATE onward, so it composes
safely with scripts/import_historical_weather_archive.py's older backfill
(1987 up to the day before HISTORY_START_DATE) regardless of which one
runs first or how many times either is re-run.

Usage (run with the backend's own venv so sqlmodel etc. are on the path):
    backend/.venv/bin/python3 scripts/import_historical_weather.py
"""
import os
import sys
from datetime import date

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from sqlmodel import Session, delete  # noqa: E402

from db import engine  # noqa: E402
from migrate import run_migrations  # noqa: E402
from models import WeatherHistory  # noqa: E402
from weather import HISTORY_START_DATE, farm_coords, fetch_historical_hourly, parse_hourly_rows  # noqa: E402


def main():
    run_migrations()
    with Session(engine) as session:
        coords = farm_coords(session)
    if coords is None:
        print("No farm location set - skipping the weather import.\n"
              "Set the farm's GPS latitude/longitude in Admin -> Settings first,\n"
              "then run this again. Nothing was imported: weather fetched for the\n"
              "wrong place would look completely normal and quietly feed the Risk\n"
              "indicator and Harvest Forecast.")
        return
    lat, lon = coords
    end_date = date.today().isoformat()
    data = fetch_historical_hourly(lat, lon, HISTORY_START_DATE, end_date)
    rows = [WeatherHistory(**r) for r in parse_hourly_rows(data, lat, lon)]
    with Session(engine) as session:
        session.exec(delete(WeatherHistory).where(WeatherHistory.timestamp >= date.fromisoformat(HISTORY_START_DATE)))
        session.add_all(rows)
        session.commit()
    print(f"Imported {len(rows)} hourly weather rows ({HISTORY_START_DATE} to {end_date}) for {lat},{lon}")


if __name__ == "__main__":
    main()
