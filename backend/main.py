"""The Boord Owner service.

One `uvicorn main:app` process, run beside Boord on the farm server. Serves
the four-tab frontend, authenticates Owner-app users against its own
data/owner.db, and returns the figures the Boord Admin Dashboard shows (minus
wages) plus the Analysis / Weather / Risk tabs - reading Boord's
data/boord.db read-only.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

import config
from db import boord_engine, init_owner_db, seed_default_manager
from routers import (analysis, auth, boord_data, dashboard, historical,
                     historical_report, risk, users, weather)

app = FastAPI(title="Boord Owner")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (auth, users, dashboard, boord_data, analysis, risk, weather,
               historical, historical_report):
    app.include_router(module.router)


# Every column this app reads from Boord's database. If Boord renames or
# drops one in a migration, the app should refuse to boot with a clear
# message rather than 500 on the first request that touches it mid-harvest.
#
# Verified against Boord v3.0-v3.1. Boord v2.x is NOT supported: v3.0 renamed
# systemsetting.farm_name/farm_location to packhouse_name/packhouse_location
# and gave a block a supplier (commit 477ab72), and this app reads the new
# names. tests/test_boord_schema.py checks this list against a real Boord
# checkout whenever one is beside this repo.
_BOORD_SCHEMA_CHECK = {
    "block": "id, name, variety, trees, hectares, active, supplier_id",
    "worker": "id, name, supplier_id, active",
    "supplier": "id, name, is_own_farm, active",
    "systemsetting": ("id, packhouse_name, packhouse_location, packhouse_code, "
                      "green_to_yellow_minutes, yellow_to_red_minutes, "
                      "current_harvest_year, season_start_month, season_start_day, "
                      "gps_lat, gps_lon"),
    "lot": ("id, slip_number, timestamp, device_id, team_id, supplier_id, driver, "
            "total_crates, total_kg, status, notes, received_at, weather_temp, "
            "weather_humidity, weather_condition, split_from_slip_number"),
    "harvestrecord": ("uuid, timestamp, worker_id, block_id, weight_kg, deduction_kg, "
                      "team_id, lot_id"),
}


def _assert_boord_schema(conn) -> None:
    for table, columns in _BOORD_SCHEMA_CHECK.items():
        try:
            conn.execute(text(f"SELECT {columns} FROM {table} LIMIT 1"))
        except Exception as e:  # noqa: BLE001 - we want to re-raise with context
            raise RuntimeError(
                f"Boord's database at {config.BOORD_DB_PATH} is missing something this "
                f"app reads from `{table}` ({columns}). Boord may have migrated its "
                f"schema past what this Owner release supports. Underlying error: {e}"
            ) from e


@app.on_event("startup")
def on_startup():
    if not os.path.exists(config.BOORD_DB_PATH):
        raise RuntimeError(
            f"BOORD_DB_PATH not found: {config.BOORD_DB_PATH}\n"
            f"Install Boord first, or set BOORD_DB_PATH to its data/boord.db."
        )
    with boord_engine.connect() as conn:
        conn.execute(text("SELECT 1"))       # fail fast if unreadable / query_only can't be set
        _assert_boord_schema(conn)
    init_owner_db()
    seed_default_manager()


class NoCacheStaticFiles(StaticFiles):
    """The dashboard is left open for days on farm devices, so force
    revalidation - a browser must not keep serving JS/HTML from before the
    last deploy. Copied from Boord's main.py."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Catch-all, must be last.
app.mount("/", NoCacheStaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
