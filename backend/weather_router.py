from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlmodel import Session, delete, select

from db import get_session
from models import WeatherHistory
from routers.historical import earliest_history_season
from security import get_current_admin
from weather import (ARCHIVE_START_DATE, HISTORY_START_DATE, different_location, farm_coords,
                      fetch_hourly_range, fetch_weather, foreign_row_count, sync_recent_weather)

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/current")
def current_weather(session: Session = Depends(get_session)):
    """Live conditions at the farm, for the header strip on every screen.

    This route used to carry its own hardcoded coordinate pair, marked as
    "for testing" and deliberately not tied to SystemSetting. That made the
    temperature in every header permanently describe one particular farm -
    not as a fallback that a correctly configured install would grow out of,
    but always, even after Settings had been filled in properly.

    It reads the configured location like everything else now, and says so
    when there isn't one rather than showing somebody else's weather.
    """
    coords = farm_coords(session)
    if coords is None:
        return {"no_location": True}
    return fetch_weather(*coords)


# ---------------------------------------------------------------------------
# Weather tab: daily-aggregated history, admin-JWT-gated. GET /history has no
# caller in Boord's own UI - the Weather tab left with the Owner View - and is
# kept as the API that app talks to; see owner-app/README.md. POST
# /history/backfill is live, and is what the setup wizard calls.
# ---------------------------------------------------------------------------

# key -> (source column on WeatherHistory, aggregation, unit, decimals).
# agg is one of "mean"/"sum"/"max", applied over a calendar day's hourly
# rows. uv_index uses the day's peak (not a mean - "how strong did it get")
# and sunshine_duration_s is summed then converted seconds->hours for a
# legible unit. weather_code/condition are categorical, not chartable as a
# line, so they're deliberately left out of this registry.
_METRICS = [
    {"key": "temp_c", "label": "Temperature", "source": "temp_c", "agg": "mean", "unit": "°C", "decimals": 1},
    {"key": "humidity_pct", "label": "Humidity", "source": "humidity_pct", "agg": "mean", "unit": "%", "decimals": 0},
    {"key": "dew_point_c", "label": "Dew Point", "source": "dew_point_c", "agg": "mean", "unit": "°C", "decimals": 1},
    {"key": "precipitation_mm", "label": "Precipitation", "source": "precipitation_mm", "agg": "sum", "unit": "mm", "decimals": 1},
    {"key": "wind_speed_kmh", "label": "Wind Speed", "source": "wind_speed_kmh", "agg": "mean", "unit": "km/h", "decimals": 1},
    {"key": "soil_temp_6cm_c", "label": "Soil Temp (6cm)", "source": "soil_temp_6cm_c", "agg": "mean", "unit": "°C", "decimals": 1},
    {"key": "uv_index", "label": "UV Index", "source": "uv_index", "agg": "max", "unit": "", "decimals": 1},
    {"key": "sunshine_hours", "label": "Sunshine", "source": "sunshine_duration_s", "agg": "sum", "unit": "hrs",
     "decimals": 1, "scale": 1 / 3600},
]


def _metrics_public() -> list:
    return [{"key": m["key"], "label": m["label"], "unit": m["unit"], "decimals": m["decimals"]} for m in _METRICS]


def build_weather_history(session: Session) -> dict:
    """Daily-aggregated WeatherHistory for the Weather tab - see _METRICS
    for per-metric aggregation. Grouped by plain calendar year (1 Jan -
    31 Dec), deliberately NOT the Aug-anchored harvest season used
    elsewhere in this app (analysis.py's _season_day) - weather doesn't
    follow the picking season the way harvest data does, and "what was the
    weather like in 2023" naturally means the calendar year. current_year
    is simply today's calendar year - the one bucket that, being still in
    progress, only covers 1 Jan through whatever's been synced so far
    rather than a full year.

    The day-grouping is done in SQL, not by reading the table into Python.
    That matters more than it looks: WeatherHistory reaches back to 1987
    (see scripts/import_historical_weather_archive.py), so hydrating every
    hourly row here meant ~350k ORM objects and ~11s per tab open - past
    the frontend's own 8s deadline (Boord.NETWORK_TIMEOUT_MS in
    shared/api.js), so the tab aborted the request and showed itself as
    offline while the server was still working. routers/risk.py bounds its
    own WeatherHistory read for the same reason; this one can't bound by
    date (the chart legitimately spans the whole record), so it aggregates
    in the database instead. SQL's aggregates skip NULLs and return NULL
    for an all-NULL day, which is exactly what the previous Python did -
    soil_temp_6cm_c and uv_index are NULL for every pre-2020 row."""
    day = func.date(WeatherHistory.timestamp).label("day")
    aggregates = []
    for m in _METRICS:
        col = getattr(WeatherHistory, m["source"])
        agg = {"mean": func.avg, "sum": func.sum, "max": func.max}[m["agg"]]
        aggregates.append(agg(col))
    rows = session.exec(select(day, *aggregates).group_by(day).order_by(day)).all()

    points = []
    for row in rows:
        d = date.fromisoformat(row[0])
        point = {"date": row[0], "year": d.year, "day_of_year": d.timetuple().tm_yday}
        for m, value in zip(_METRICS, row[1:]):
            point[m["key"]] = None if value is None else round(value * m.get("scale", 1), m["decimals"])
        points.append(point)

    last_synced = session.exec(select(func.max(WeatherHistory.timestamp))).one()

    # Hours on file that were fetched for somewhere other than where this
    # farm now says it is. Normally zero; anything else means the GPS was
    # corrected after weather had already been downloaded, and the chart
    # above is a blend of two places until the backfill is re-run. Reported
    # here because the Weather tab is where somebody would notice the
    # numbers looking wrong and have no way to find out why.
    coords = farm_coords(session)
    hours_elsewhere = foreign_row_count(session, *coords) if coords else 0

    return {
        "metrics": _metrics_public(),
        "years": sorted({p["year"] for p in points}),
        "current_year": date.today().year,
        "last_synced": last_synced.isoformat() if last_synced else None,
        "hours_elsewhere": hours_elsewhere,
        "points": points,
    }


@router.get("/history")
def weather_history(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Admin-JWT-gated Weather tab data - syncs the latest hours from
    Open-Meteo first (best-effort, see weather.sync_recent_weather) then
    returns the full daily-aggregated history."""
    sync_recent_weather(session)
    return build_weather_history(session)


@router.post("/history/backfill")
def backfill_weather_history(years: Optional[int] = Query(None, ge=1, le=200),
                              session: Session = Depends(get_session),
                              admin=Depends(get_current_admin)):
    """Pull the weather record for the farm's location, `years` back to today.

    Same job as scripts/import_historical_weather.py and its 1987-2019
    sibling, reachable from the browser - the setup wizard offers it once
    GPS has been entered, because a new customer has no shell on the server
    and no reason to know those scripts exist. The ordering is the point:
    this cannot run before the location step, so the "imported weather for
    the wrong place" failure that farm_coords() refuses to allow never gets
    a chance to arise.

    `years` counts calendar years including this one, and is what the wizard
    asks the farm for: the trade is real and only they can make it, because
    the useful depth is set by their own harvest history (routers/risk.py
    scores every reference season that has yield data, and refuses one it
    has no weather for) while the cost is download time on a farm's
    internet. Omitted, it means HISTORY_START_DATE onward - the range this
    endpoint fetched before it could be asked, and what weather.py's own
    default covers. Clamped at ARCHIVE_START_DATE, so asking for more years
    than exist is not an error, it just starts in 1987.

    Everything it fetches, it replaces: the whole requested range, plus -
    anywhere in the table, at any date - the rows that were fetched for a
    different location. That second one is the point of the lat/lon columns.
    A farm that corrects its GPS has a table holding two places' weather,
    and there is no reading of "keep it" that helps anybody: the Risk
    indicator would score this season against last season's other town.
    Rows OUTSIDE the requested range that belong here are left alone, which
    is what keeps this composable with the archive script in either order.

    Slow by nature - a year is ~8,760 rows - so callers must set a timeout
    to match what they asked for, not Boord's 8s default.
    """
    coords = farm_coords(session)
    if coords is None:
        # Not an error: the wizard offers this button before the location
        # step is necessarily done, and "set your location first" is the
        # honest answer rather than a 400.
        return {"no_location": True, "imported": 0}
    lat, lon = coords

    end = date.today()
    floor = date.fromisoformat(ARCHIVE_START_DATE)
    if years is None:
        start = date.fromisoformat(HISTORY_START_DATE)
    else:
        start = date(max(end.year - years + 1, floor.year), 1, 1)

    try:
        rows = [WeatherHistory(**r) for r in fetch_hourly_range(lat, lon, start, end)]
    except Exception as e:
        # The farm server's internet is genuinely unreliable, and a long
        # range is several requests, any of which can be the one that drops.
        # Say so and leave the existing history alone - a half-deleted table
        # would be worse than no import.
        raise HTTPException(502, f"Could not reach the weather service ({type(e).__name__}). "
                                  f"Nothing was changed - try again later.")

    # Counted before the delete, and only outside the range being replaced:
    # foreign rows inside it were going to be overwritten anyway, so
    # reporting them would turn an ordinary re-run into an alarming number.
    removed_elsewhere = session.exec(
        select(func.count()).select_from(WeatherHistory)
        .where(different_location(lat, lon), WeatherHistory.timestamp < start)
    ).one()

    session.exec(delete(WeatherHistory).where(WeatherHistory.timestamp >= start))
    session.exec(delete(WeatherHistory).where(different_location(lat, lon)))
    session.add_all(rows)
    session.commit()
    return {"imported": len(rows), "start_date": start.isoformat(), "end_date": end.isoformat(),
            "years": end.year - start.year + 1,
            "lat": lat, "lon": lon,
            "removed_elsewhere": removed_elsewhere,
            "uncovered_season": _uncovered_season(session, start.year)}


def _uncovered_season(session: Session, start_year: int) -> Optional[int]:
    """The earliest harvest season this farm has imported, if that is before
    the weather now on file - otherwise None.

    Worth reporting rather than leaving to be discovered. routers/risk.py
    scores every reference season from REFERENCE_START_YEAR (2012) onward
    that has yield data, and it needs weather for each one: a farm that
    imports season totals back to, say, 2013 and then fetches weather only
    from 2020 gets a Risk indicator that raises "no weather data for
    reference season 2013" instead of a score. Nothing warns them, because
    each half looks like it worked.

    This used to be able to say only "run update_server.bat", because the
    older range lives behind a different Open-Meteo API and was fetched
    exclusively by a shell script. Now that the caller chooses its own
    depth, the answer is simply to choose more years.
    """
    earliest = earliest_history_season(session)
    if earliest is None:
        return None
    return earliest if earliest < start_year else None
