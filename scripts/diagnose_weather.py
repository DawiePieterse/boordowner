#!/usr/bin/env python3
"""Time the Weather tab's endpoint, stage by stage.

/api/weather/history is the slowest thing this app serves, and when it
overruns the browser's deadline the tab reports itself OFFLINE rather than
slow - so the symptom names the network instead of the query. This says
where the time actually goes.

Run on the server that has the data:
    backend\\.venv\\Scripts\\python.exe scripts\\diagnose_weather.py

Prints a timing for each stage the endpoint performs, plus the size of the
payload it would send.

NOT read-only, and deliberately so: it runs the real sync_recent_weather(),
which appends any hours missing since the last one. That is exactly what
opening the tab does, so timing anything else would be measuring a
different thing - but it does mean this writes to owner.db when the record
is behind. It never deletes or rewrites.
"""
import io
import json
import os
import sys
import time

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from fastapi.encoders import jsonable_encoder  # noqa: E402
from sqlmodel import Session, func, select  # noqa: E402

import config  # noqa: E402
from db import boord_engine, owner_engine  # noqa: E402
from models_owner import WeatherHistory  # noqa: E402
from weather import (farm_coords, foreign_row_count,  # noqa: E402
                     sync_recent_weather)
from routers.weather import build_weather_history  # noqa: E402


class Timer:
    def __init__(self, label):
        self.label = label

    def __enter__(self):
        print(f"  {self.label:<44}", end="", flush=True)
        self.t = time.time()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.time() - self.t
        mark = "  <-- SLOW" if self.elapsed > 3 else ""
        print(f"{self.elapsed:8.2f}s{mark}")


def main():
    print(f"\nowner.db : {config.OWNER_DB_PATH}")
    try:
        size_mb = os.path.getsize(config.OWNER_DB_PATH) / 1024 / 1024
        print(f"           {size_mb:,.1f} MB on disk")
    except OSError:
        pass
    print(f"boord.db : {config.BOORD_DB_PATH}\n")

    with Session(owner_engine) as owner:
        with Timer("count weather rows"):
            n = owner.exec(select(func.count()).select_from(WeatherHistory)).one()
        print(f"       -> {n:,} hourly rows")

        with Timer("newest row (indexed lookup)"):
            latest = owner.exec(
                select(WeatherHistory).order_by(WeatherHistory.timestamp.desc())).first()
        print(f"       -> {latest.timestamp if latest else 'none'}")

    # Each stage gets its own sessions, so one stage's connection state
    # cannot colour the next one's timing.
    with Session(owner_engine) as owner, Session(boord_engine) as boord:
        with Timer("sync_recent_weather (may call Open-Meteo)") as t_sync:
            result = sync_recent_weather(owner, boord)
        print(f"       -> {result}")

    with Session(boord_engine) as boord:
        coords = farm_coords(boord)
    print(f"  farm coordinates: {coords}")

    with Session(owner_engine) as owner:
        with Timer("foreign_row_count (full scan, no index)"):
            foreign = foreign_row_count(owner, *coords) if coords else 0
        print(f"       -> {foreign:,} rows recorded for another location")

    with Session(owner_engine) as owner, Session(boord_engine) as boord:
        with Timer("build_weather_history (the whole endpoint)") as t_build:
            data = build_weather_history(owner, boord)
        print(f"       -> {len(data['points']):,} daily points, "
              f"{len(data['years'])} years")

    # FastAPI's own encoder, not a plain json.dumps - it walks the whole
    # structure and is markedly slower, so timing dumps alone would flatter
    # the endpoint.
    with Timer("jsonable_encoder (what FastAPI actually runs)") as t_enc:
        encoded = jsonable_encoder(data)

    with Timer("JSON serialisation") as t_json:
        body = json.dumps(encoded, default=str)
    print(f"       -> {len(body) / 1024 / 1024:,.1f} MB payload")

    # A NaN or Infinity anywhere in the payload is emitted by Python as a
    # bare NaN, which is not valid JSON. The browser's JSON.parse then throws
    # PART WAY THROUGH a response that looked fine at the HTTP level - which
    # surfaces as a failed request rather than as a data problem, and can
    # read as "the server is unreachable". Worth knowing about explicitly.
    print(f"  {'strict JSON check (NaN / Infinity)':<44}", end="", flush=True)
    try:
        json.dumps(encoded, allow_nan=False)
        print("       ok")
        bad_json = False
    except ValueError as e:
        print(f"   FAILED: {e}")
        print("       -> the payload contains NaN or Infinity, which is NOT")
        print("          valid JSON. This is the bug, not the timing.")
        bad_json = True

    total = t_sync.elapsed + t_build.elapsed + t_enc.elapsed + t_json.elapsed
    print(f"\n  TOTAL the browser waits for:              {total:8.2f}s")
    if total > 45:
        print("  Past the tab's 45s deadline - it will report itself OFFLINE.")
    elif total > 8:
        print("  Past the OLD 8s deadline, inside the current 45s one.")
    elif not bad_json:
        print("  Comfortably inside the deadline - the slowness is NOT here.")
    print()


if __name__ == "__main__":
    main()
