from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_
from sqlmodel import Session, delete, select

from db import get_boord_session, get_owner_session, weather_append_lock
from models_owner import WeatherHistory
from routers.historical import earliest_history_season
from security import get_current_manager, get_current_user
from weather import (ARCHIVE_START_DATE, HISTORY_START_DATE, different_location, farm_coords,
                      farm_coords_and_release, fetch_hourly_range, fetch_weather_cached,
                      foreign_row_count, sync_recent_weather)

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/current")
def current_weather(boord: Session = Depends(get_boord_session), user=Depends(get_current_user)):
    """Live conditions at the farm, for the header strip.

    Cached (fetch_weather_cached, ~10 min TTL) rather than fetched fresh: the
    header refreshes on every dashboard load and every pull-to-refresh, and
    several owners may have the page open at once, but Open-Meteo only
    updates every ~15 minutes anyway. Uncached, this was one upstream request
    per screen refresh per person for data that had not changed."""
    coords = farm_coords_and_release(boord)
    if coords is None:
        return {"no_location": True}
    return fetch_weather_cached(*coords)


# ---------------------------------------------------------------------------
# Weather tab: daily-aggregated history. GET /history is what the Weather tab
# loads; POST /history/backfill rebuilds the stored record wholesale and is
# manager-only.
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


def _years_on_file(owner: Session) -> list:
    """Every calendar year WeatherHistory holds an hour for.

    Its own query rather than a by-product of the points, because the points
    are now only the years being charted while this list drives the filter
    row - all forty of them have to be tickable whether or not they are
    currently drawn.
    """
    rows = owner.exec(
        select(func.strftime("%Y", WeatherHistory.timestamp)).distinct()).all()
    return sorted(int(y) for y in rows if y)


def build_weather_history(owner: Session, boord: Session,
                          years: Optional[list] = None) -> dict:
    """Daily-aggregated WeatherHistory for the Weather tab - see _METRICS
    for per-metric aggregation. Grouped by plain calendar year (1 Jan -
    31 Dec), deliberately NOT the Aug-anchored harvest season used
    elsewhere in this app (analysis.py's _season_day) - weather doesn't
    follow the picking season the way harvest data does, and "what was the
    weather like in 2023" naturally means the calendar year. current_year
    is simply today's calendar year - the one bucket that, being still in
    progress, only covers 1 Jan through whatever's been synced so far
    rather than a full year.

    `years` is the calendar years to actually chart, defaulting to the most
    recent one on file. The chart overlays a handful of years at a time and
    never draws the whole record at once, so returning all of it meant
    ~14,500 daily points and a 3.2 MB response on every tab open, of which
    the tab used one year. The filter list still covers everything (see
    _years_on_file) - the frontend fetches a year the first time it is
    ticked and keeps it, so ticking one back off and on again costs nothing.

    The day-grouping is done in SQL, not by reading the table into Python.
    That matters more than it looks: WeatherHistory reaches back to 1987
    (see scripts/import_historical_weather_archive.py), so hydrating every
    hourly row here meant ~350k ORM objects and ~11s per tab open - past
    the frontend's own deadline (Boord.NETWORK_TIMEOUT_MS in
    shared/api.js), so the tab aborted the request and showed itself as
    offline while the server was still working. routers/risk.py bounds its
    own WeatherHistory read for the same reason. SQL's aggregates skip NULLs
    and return NULL for an all-NULL day, which is exactly what the previous
    Python did - soil_temp_6cm_c and uv_index are NULL for every pre-2020
    row."""
    all_years = _years_on_file(owner)
    # Unknown years are dropped rather than rejected: the filter row is built
    # from a list this same endpoint returned, so a year that has since gone
    # is a stale tab, not a bad request.
    wanted = [y for y in (years or all_years[-1:]) if y in set(all_years)]
    # ...but dropping every year asked for must not fall through to "no year
    # filter at all", which is the whole record: a stale tab asking only for a
    # year since removed would be answered with forty years and 3.2 MB, the
    # very thing the parameter exists to stop. It gets the same default a
    # fresh tab does.
    wanted = wanted or all_years[-1:]

    day = func.date(WeatherHistory.timestamp).label("day")
    aggregates = []
    for m in _METRICS:
        col = getattr(WeatherHistory, m["source"])
        agg = {"mean": func.avg, "sum": func.sum, "max": func.max}[m["agg"]]
        aggregates.append(agg(col))

    query = select(day, *aggregates)
    # Empty only when WeatherHistory itself is - the fallback above means a
    # request always resolves to a year while there is one on file.
    if wanted:
        # One indexed range per year, OR'd - not strftime(timestamp) IN (...),
        # which is a function on the column and would scan the whole table to
        # chart a single year. timestamp is indexed; these ranges use it.
        query = query.where(or_(*[
            and_(WeatherHistory.timestamp >= datetime(y, 1, 1),
                 WeatherHistory.timestamp < datetime(y + 1, 1, 1))
            for y in wanted]))
    rows = owner.exec(query.group_by(day).order_by(day)).all()

    points = []
    for row in rows:
        d = date.fromisoformat(row[0])
        point = {"date": row[0], "year": d.year, "day_of_year": d.timetuple().tm_yday}
        for m, value in zip(_METRICS, row[1:]):
            point[m["key"]] = None if value is None else round(value * m.get("scale", 1), m["decimals"])
        points.append(point)

    last_synced = owner.exec(select(func.max(WeatherHistory.timestamp))).one()

    # Hours on file that were fetched for somewhere other than where this
    # farm now says it is. Normally zero; anything else means the GPS was
    # corrected after weather had already been downloaded, and the chart
    # above is a blend of two places until the backfill is re-run. Reported
    # here because the Weather tab is where somebody would notice the
    # numbers looking wrong and have no way to find out why.
    coords = farm_coords(boord)
    hours_elsewhere = foreign_row_count(owner, *coords) if coords else 0

    return {
        "metrics": _metrics_public(),
        "years": all_years,
        "years_returned": wanted,
        "current_year": date.today().year,
        "last_synced": last_synced.isoformat() if last_synced else None,
        "hours_elsewhere": hours_elsewhere,
        "points": points,
    }


@router.get("/history")
def weather_history(years: Optional[str] = Query(
                        None, description="Comma-separated calendar years to chart, "
                                          "e.g. 2024,2025. Defaults to the most recent."),
                    owner: Session = Depends(get_owner_session),
                    boord: Session = Depends(get_boord_session),
                    user=Depends(get_current_user)):
    """Weather tab data - syncs the latest hours from Open-Meteo first
    (best-effort, see weather.sync_recent_weather) then returns the daily
    aggregate for the requested years.

    `years` is a comma-separated string rather than a repeated query
    parameter so the frontend can build it by joining its selection, and so
    a URL with a dozen years in it stays readable in a log. Anything
    unparseable is ignored rather than rejected - see build_weather_history
    on why a stale year is a stale tab, not a bad request."""
    wanted = []
    for part in (years or "").split(","):
        part = part.strip()
        if part.isdigit():
            wanted.append(int(part))
    sync_recent_weather(owner, boord)
    return build_weather_history(owner, boord, years=wanted or None)


@router.post("/history/backfill")
def backfill_weather_history(years: Optional[int] = Query(None, ge=1, le=200),
                              owner: Session = Depends(get_owner_session),
                              boord: Session = Depends(get_boord_session),
                              mgr=Depends(get_current_manager)):
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
    # Released before the fetch: a full 1987-onward range is several chunked
    # requests at a 120s timeout each, and Boord's database must not be held
    # open for minutes. See farm_coords_and_release().
    coords = farm_coords_and_release(boord)
    if coords is None:
        # Not an error: "set your location first" is the honest answer.
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
    removed_elsewhere = owner.exec(
        select(func.count()).select_from(WeatherHistory)
        .where(different_location(lat, lon), WeatherHistory.timestamp < start)
    ).one()

    with weather_append_lock:
        owner.exec(delete(WeatherHistory).where(WeatherHistory.timestamp >= start))
        owner.exec(delete(WeatherHistory).where(different_location(lat, lon)))
        owner.add_all(rows)
        owner.commit()
    return {"imported": len(rows), "start_date": start.isoformat(), "end_date": end.isoformat(),
            "years": end.year - start.year + 1,
            "lat": lat, "lon": lon,
            "removed_elsewhere": removed_elsewhere,
            "uncovered_season": _uncovered_season(owner, start.year)}


def _uncovered_season(owner: Session, start_year: int) -> Optional[int]:
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
    earliest = earliest_history_season(owner)
    if earliest is None:
        return None
    return earliest if earliest < start_year else None
