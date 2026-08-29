#!/usr/bin/env python3
"""Backfill of even-older hourly weather (1987 up to the day before
scripts/import_historical_weather.py's own HISTORY_START_DATE) into the
WeatherHistory table, from Open-Meteo's reanalysis-based archive API -
Open-Meteo's historical-forecast API (used by the 2020-onward import)
rejects any start_date before 2016.

1987 matches the earliest year in HistoricalAnnualYield (see
scripts/import_historical_annual_yield.py) - there's no harvest data
before that to correlate against, so no point fetching further back.

Two caveats vs. the 2020-onward import:
- soil_temperature_6cm and uv_index are never available from this archive
  endpoint at any date (confirmed by hand) - every row this script inserts
  has those two columns NULL. weather.ARCHIVE_HOURLY_FIELDS reflects this.
- Like the historical harvest data itself, this is reference-only: the
  Risk indicator and Harvest Forecast are fixed to a 2020-2025 reference
  range (see routers/risk.py), so none of this backfill changes their
  output - it only extends what the Weather tab can chart.

Fetched in 5-year chunks (33 years of hourly data in one request works but
is a large, slow, all-or-nothing call - chunking keeps a network hiccup
from forcing a full retry, and is gentler on Open-Meteo's API).

Only ever deletes/reinserts its own range (ARCHIVE_START_DATE up to the
day before HISTORY_START_DATE), so it composes safely with
scripts/import_historical_weather.py regardless of run order.

Safe to re-run any time - and cheap to, since this range is finalized and
never changes: if the DB already has an hour at each end of the range
(the first hour of ARCHIVE_START_DATE and the last hour of the day before
HISTORY_START_DATE) AND those hours were fetched for where the farm now
says it is, the whole fetch is skipped rather than re-running all 7
chunked API calls on every server update for data that can't have changed.
Pass --force to re-fetch anyway.

That location check is why the rows carry lat/lon. This skip used to be
unconditional, so correcting the farm's GPS in Settings left 33 years of
another place's weather in the table for good unless somebody knew to come
and run this by hand with --force. Now the farm corrects its coordinates
and the next update_server.bat refetches on its own.

Usage (run with the backend's own venv so sqlmodel etc. are on the path):
    backend/.venv/bin/python3 scripts/import_historical_weather_archive.py [--force]
"""
import os
import sys
from datetime import date, datetime, timedelta

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from sqlmodel import Session, delete, select  # noqa: E402

from db import engine  # noqa: E402
from migrate import run_migrations  # noqa: E402
from models import WeatherHistory  # noqa: E402
from weather import (ARCHIVE_CHUNK_YEARS, ARCHIVE_START_DATE, HISTORY_START_DATE,  # noqa: E402
                      at_location, chunk_date_range, farm_coords, fetch_archive_hourly,
                      parse_hourly_rows)


def already_imported(session, lat, lon) -> bool:
    """True only if this range is on file AND was fetched for this place."""
    range_start = date.fromisoformat(ARCHIVE_START_DATE)
    range_end = date.fromisoformat(HISTORY_START_DATE) - timedelta(days=1)
    first_hour = datetime(range_start.year, range_start.month, range_start.day, 0)
    last_hour = datetime(range_end.year, range_end.month, range_end.day, 23)
    for hour in (first_hour, last_hour):
        row = session.exec(select(WeatherHistory).where(WeatherHistory.timestamp == hour)).first()
        if row is None or not at_location(row.lat, row.lon, lat, lon):
            return False
    return True


def load_rows(lat, lon):
    start = date.fromisoformat(ARCHIVE_START_DATE)
    end = date.fromisoformat(HISTORY_START_DATE) - timedelta(days=1)
    rows = []
    for chunk_start, chunk_end in chunk_date_range(start, end, ARCHIVE_CHUNK_YEARS):
        print(f"  fetching {chunk_start} to {chunk_end}...")
        data = fetch_archive_hourly(lat, lon, chunk_start.isoformat(), chunk_end.isoformat())
        rows += [WeatherHistory(**r) for r in parse_hourly_rows(data, lat, lon)]
    return rows


def main():
    run_migrations()
    force = "--force" in sys.argv
    with Session(engine) as session:
        # The location comes first now: whether this range is already
        # imported depends on where it was imported FOR.
        coords = farm_coords(session)
        if coords is None:
            print("No farm location set - skipping the weather import.\n"
                  "Set the farm's GPS latitude/longitude in Admin -> Settings first,\n"
                  "then run this again. Nothing was imported: weather fetched for the\n"
                  "wrong place would look completely normal and quietly feed the Risk\n"
                  "indicator and Harvest Forecast.")
            return
        lat, lon = coords
        if not force and already_imported(session, lat, lon):
            print(f"Archive weather already covers {ARCHIVE_START_DATE} to "
                  f"{(date.fromisoformat(HISTORY_START_DATE) - timedelta(days=1)).isoformat()} "
                  f"for {lat},{lon} - skipping (pass --force to re-fetch).")
            return
    rows = load_rows(lat, lon)
    with Session(engine) as session:
        session.exec(delete(WeatherHistory).where(WeatherHistory.timestamp < date.fromisoformat(HISTORY_START_DATE)))
        session.add_all(rows)
        session.commit()
    print(f"Imported {len(rows)} hourly weather rows ({ARCHIVE_START_DATE} to "
          f"{(date.fromisoformat(HISTORY_START_DATE) - timedelta(days=1)).isoformat()}) for {lat},{lon}")


if __name__ == "__main__":
    main()
