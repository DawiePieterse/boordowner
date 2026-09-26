import json as _json
import re as _re
import threading
import time as _time
import urllib.request
from datetime import date, datetime, timedelta
from typing import Iterator, Optional

from sqlalchemy import func, or_
from sqlmodel import Session, select

import config
import db
from models_boord import SystemSetting
from models_owner import WeatherHistory

_WMO_CONDITION = {
    0: "Clear", 1: "Partly Cloudy", 2: "Partly Cloudy", 3: "Overcast",
    45: "Foggy", 48: "Foggy",
    51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    61: "Rain", 63: "Rain", 65: "Heavy Rain",
    71: "Snow", 73: "Snow", 75: "Heavy Snow",
    80: "Showers", 81: "Showers", 82: "Heavy Showers",
    95: "Storm", 96: "Storm", 99: "Storm",
}


def _fetch_open_meteo_current(lat: float, lon: float) -> dict:
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,relative_humidity_2m,weather_code"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.loads(resp.read())
        curr = data.get("current", {})
        code = int(curr.get("weather_code", 0))
        condition = _WMO_CONDITION.get(code, "Cloudy")
        return {
            "temp": curr.get("temperature_2m"),
            "humidity": curr.get("relative_humidity_2m"),
            "condition": condition,
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# The farm's own on-site station (iWeathar). No JSON API is published for it
# - display?s_id=<id> (a plain HTML page meant for a browser) is the only
# interface, so this scrapes it. Real sensor readings for this exact spot
# beat Open-Meteo's grid-cell estimate, so fetch_weather() below prefers
# these wherever the station actually measures something, and only asks
# Open-Meteo to fill in what it can't (a cloud-based condition - the station
# has no sky sensor - and everywhere this farm has no station configured or
# it's unreachable).
# ---------------------------------------------------------------------------

IWEATHAR_URL = "https://iweathar.co.za/display"

# The page's numbers live inside `class='numbers'>VALUE`, immediately after
# the plain-text label naming the field ("Temperature:", "Rainfall Today:",
# ...) - stable across the page's otherwise-loose HTML (mismatched quotes,
# inconsistent tag case). This finds the label, then reads the first
# `class='numbers'>` within a short window after it, rather than trying to
# parse the markup properly - there is no structure here worth a real parser.
_NUMBERS_RE = _re.compile(r"class=['\"]numbers['\"][^>]*>\s*(-?\d+(?:\.\d+)?)", _re.IGNORECASE)


def _num_after(html_lower: str, label: str, window: int = 250) -> Optional[float]:
    idx = html_lower.find(label.lower())
    if idx == -1:
        return None
    m = _NUMBERS_RE.search(html_lower[idx: idx + window])
    return float(m.group(1)) if m else None


def _condition_from_rain_today_mm(mm: float) -> Optional[str]:
    """A same-vocabulary condition (see _WMO_CONDITION) from the station's
    own rain gauge, for when it has actually measured rain today. Thresholds
    are a daily total, not comparable to the hourly ones weather codes use -
    kept separate on purpose rather than reusing that table."""
    if mm <= 0:
        return None
    if mm <= 2:
        return "Drizzle"
    if mm <= 10:
        return "Rain"
    return "Heavy Rain"


def fetch_iweathar_current(station_id: str, timeout: int = 5) -> dict:
    """Live conditions from the farm's own iWeathar station.

    Returns {} on any network or parse failure, or if the page didn't yield
    at least a temperature and a humidity reading - same "never block the
    caller" contract as _fetch_open_meteo_current() above, and fetch_weather()
    treats an empty dict here exactly like a dead Open-Meteo call.
    """
    try:
        url = f"{IWEATHAR_URL}?s_id={station_id}"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            html = resp.read().decode("iso-8859-1", errors="replace").lower()

        temp = _num_after(html, "Temperature:")
        humidity = _num_after(html, "Humidity:")
        if temp is None or humidity is None:
            return {}

        result = {"temp": temp, "humidity": humidity, "source": "iweathar"}

        rain_today = _num_after(html, "Rainfall Today:")
        if rain_today is not None:
            result["rain_today_mm"] = rain_today
            condition = _condition_from_rain_today_mm(rain_today)
            if condition:
                result["condition"] = condition

        for label, key in (
            ("Dew Point:", "dew_point_c"),
            ("Barometer:", "pressure_mb"),
            ("Wind Gust:", "wind_gust_kmh"),
            ("Wind Average:", "wind_avg_kmh"),
            ("Min Temp:", "temp_min_c"),
            ("Max Temp:", "temp_max_c"),
        ):
            value = _num_after(html, label)
            if value is not None:
                result[key] = value

        return result
    except Exception:
        return {}


def _ttl_cached(cache: dict, lock: threading.Lock, key, fetch, ttl: int = 600, ttl_on_failure: int = 60):
    """(expiry, value) TTL cache shared by the station scrape and the blended
    reading below. An empty (failed) result is cached for ttl_on_failure only,
    so a dropped link doesn't stall every caller on a timeout."""
    now = _time.monotonic()
    with lock:
        hit = cache.get(key)
        if hit and now < hit[0]:
            return hit[1]

    value = fetch()

    with lock:
        cache[key] = (now + (ttl if value else ttl_on_failure), value)
    return value


# Same "don't hammer a hobbyist's server every request" reasoning as
# fetch_weather_cached() below, applied to the station scrape on its own -
# routers/risk.py reads today's rain/temp extremes from this same cache to
# correct the in-progress season's still-open driver windows (see
# routers/risk.py's DRIVERS and _driver_value's today/station_today
# parameters), so a Risk tab load and a dashboard load share one cached
# reading instead of each scraping the page separately.
_station_cache: dict = {}
_station_cache_lock = threading.Lock()


def fetch_iweathar_current_cached(station_id: str) -> dict:
    return _ttl_cached(_station_cache, _station_cache_lock, station_id,
                       lambda: fetch_iweathar_current(station_id))


def farm_station_reading() -> dict:
    """This install's own station reading (cached), or {} when no station is
    configured (config.IWEATHAR_STATION_ID unset)."""
    return fetch_iweathar_current_cached(config.IWEATHAR_STATION_ID) if config.IWEATHAR_STATION_ID else {}


def fetch_weather(lat: float, lon: float) -> dict:
    """Current conditions for the header strip. Blends the farm's own
    iWeathar station (config.IWEATHAR_STATION_ID - real sensor readings for
    this exact spot) with Open-Meteo (a modelled estimate for the
    coordinates, and the only source for a cloud-based condition, since the
    station has no sky sensor). The station wins wherever it has a reading;
    Open-Meteo fills in whatever it doesn't. A farm with no station
    configured, or one that's unreachable, gets exactly the old
    Open-Meteo-only behaviour.
    """
    station = farm_station_reading()
    # A station reading always has temp and humidity (fetch_iweathar_current
    # returns {} otherwise); when it also has a rain-derived condition,
    # Open-Meteo has nothing left to contribute.
    meteo = {} if "condition" in station else _fetch_open_meteo_current(lat, lon)
    if not station and not meteo:
        return {}
    # Station wins wherever it has a reading - including its rain-gauge
    # condition, which beats the model's weather code for "is it raining at
    # THIS farm" (a local shower can be under Open-Meteo's radar).
    return {**{k: meteo.get(k) for k in ("temp", "humidity", "condition")}, **station}


# A field device syncs a whole batch of crates at once and every crate gets
# stamped with the conditions (routers/sync.py), so an uncached lookup would
# mean one HTTP round trip per crate - hundreds on a busy morning, each one
# holding up the sync. The upstream service only refreshes every ~15 minutes,
# so a short cache costs nothing in accuracy. Failures are cached briefly too,
# so a dropped link doesn't stall every following crate on a 5s timeout.
_cache: dict = {}
_cache_lock = threading.Lock()


def fetch_weather_cached(lat: float, lon: float) -> dict:
    return _ttl_cached(_cache, _cache_lock, (round(lat, 4), round(lon, 4)),
                       lambda: fetch_weather(lat, lon))


# ---------------------------------------------------------------------------
# Historical weather (hourly backfill + Weather tab). Shared by
# scripts/import_historical_weather.py (wholesale replace, run by hand) and
# sync_recent_weather() below (append-only, run as a side effect of opening
# the Weather tab) so both go through one fetch/parse implementation.
# ---------------------------------------------------------------------------

HISTORY_START_DATE = "2020-01-01"

# The oldest weather anyone can ask for. Open-Meteo's archive reaches back to
# 1940; 1987 is where this app stops because it is the earliest season any
# farm has harvest data for to correlate against (see
# scripts/import_historical_annual_yield.py). Lived in that script's sibling
# until the setup wizard started letting a farm choose its own depth.
ARCHIVE_START_DATE = "1987-01-01"

# Chunk size for the pre-2020 archive fetch. 33 years in one request works
# but is a large, slow, all-or-nothing call - chunking keeps a network hiccup
# from forcing a full retry, and is gentler on Open-Meteo's API.
ARCHIVE_CHUNK_YEARS = 5

# How far two coordinate pairs may differ and still count as the same place:
# ~11 m. Coordinates are stored exactly as they were requested, so an
# untouched Settings value compares equal on the nose; this only stops a
# re-typed final decimal from invalidating a farm's whole weather history.
COORD_TOLERANCE = 0.0001

HOURLY_FIELDS = ",".join([
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation",
    "weather_code", "wind_speed_10m", "soil_temperature_6cm", "uv_index",
    "sunshine_duration",
])

def farm_coords(boord: Session) -> Optional[tuple]:
    """The farm's GPS position from Boord's Settings, or None if it isn't set
    yet. Reads Boord's SystemSetting, so it takes a boord_session.

    This used to fall back to a fixed pair of coordinates when Settings was
    blank. That was survivable while there was one farm, because the fallback
    WAS that farm. As a product it is a silent correctness bug: a farm that
    hasn't filled in its location gets a different farm's weather, and since
    the Risk indicator and Harvest Forecast are computed from that weather,
    they produce confident scores describing somewhere else entirely. Nothing
    errors, nothing looks wrong, and the numbers are simply about the wrong
    place.

    So there is no fallback. Every caller has to decide what to do with no
    location, and none of them is allowed to invent one.

    Note the `is not None` checks: a plain truthiness test treats latitude 0
    (the equator) and longitude 0 (Greenwich) as "unset".
    """
    settings = boord.exec(select(SystemSetting)).first()
    if settings and settings.gps_lat is not None and settings.gps_lon is not None:
        return settings.gps_lat, settings.gps_lon
    return None


def farm_coords_and_release(boord: Session) -> Optional[tuple]:
    """farm_coords(), then immediately hand the Boord connection back.

    Every caller of this is about to make an Open-Meteo request - a few
    seconds for a forecast, minutes for a full 1987-onward backfill - and
    Boord's database must not be held open across that. Boord migrates it on
    its own startup and copies it before doing so; a read left open through
    one of those would see the schema move underneath it.

    Session.close() is idempotent, and the caller's session is dead to us
    afterwards by convention: read the coordinates, let go, then fetch.
    """
    coords = farm_coords(boord)
    boord.close()
    return coords


def different_location(lat: float, lon: float):
    """SQL condition matching the WeatherHistory rows that are NOT this place's.

    The one definition of "somebody else's weather", shared by the Weather
    tab (which reports how many such rows there are), the browser backfill
    (which deletes them) and the archive import script (which stops skipping
    itself when it finds any). A NULL coordinate counts as different - see
    the lat/lon comment on the model for why unknown provenance is treated
    as foreign rather than as "probably ours".
    """
    return or_(
        WeatherHistory.lat.is_(None),
        WeatherHistory.lon.is_(None),
        func.abs(WeatherHistory.lat - lat) > COORD_TOLERANCE,
        func.abs(WeatherHistory.lon - lon) > COORD_TOLERANCE,
    )


def at_location(row_lat, row_lon, lat: float, lon: float) -> bool:
    """The in-Python counterpart of different_location(), for a row already
    loaded. Kept beside it so the two cannot drift apart."""
    if row_lat is None or row_lon is None:
        return False
    return abs(row_lat - lat) <= COORD_TOLERANCE and abs(row_lon - lon) <= COORD_TOLERANCE


def foreign_row_count(session: Session, lat: float, lon: float) -> int:
    return session.exec(
        select(func.count()).select_from(WeatherHistory).where(different_location(lat, lon))
    ).one()


def chunk_date_range(start: date, end: date, years: int) -> Iterator[tuple]:
    """[start, end] split into calendar-aligned chunks of `years` years."""
    cur = start
    while cur <= end:
        chunk_end = min(date(cur.year + years, 1, 1) - timedelta(days=1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def _get_json(url: str, timeout: int) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return _json.loads(resp.read())


def fetch_historical_hourly(lat: float, lon: float, start_date: str, end_date: str, timeout: int = 120) -> dict:
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}"
        f"&hourly={HOURLY_FIELDS}&timezone=auto"
    )
    return _get_json(url, timeout)


# Sibling of fetch_historical_hourly() above, for dates before that API's own
# 2016-01-01 floor (HISTORY_START_DATE / scripts/import_historical_weather.py
# only ever asks it for 2020 onward, so that floor has never mattered until
# now). Hits Open-Meteo's separate reanalysis-based archive instead, which
# reaches back to 1940 - but doesn't carry soil_temperature_6cm or uv_index
# at any date (confirmed by hand: both come back all-null even for recent
# dates), so ARCHIVE_HOURLY_FIELDS omits them rather than requesting fields
# that can never be filled. Those two columns are simply NULL for any row
# this fetches - parse_hourly_rows() already pads missing hourly series with
# None, so no other change was needed to reuse it here.
ARCHIVE_HOURLY_FIELDS = ",".join([
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation",
    "weather_code", "wind_speed_10m", "sunshine_duration",
])


def fetch_archive_hourly(lat: float, lon: float, start_date: str, end_date: str, timeout: int = 120) -> dict:
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}&start_date={start_date}&end_date={end_date}"
        f"&hourly={ARCHIVE_HOURLY_FIELDS}&timezone=auto"
    )
    return _get_json(url, timeout)


# Sibling of fetch_historical_hourly() above, for routers/risk.py's Harvest
# Forecast - but hits the REAL forecast host (not the historical-forecast
# one). Raises on failure rather than swallowing it, like
# fetch_historical_hourly() and unlike fetch_weather() below - the caller
# (build_harvest_forecast) decides the fallback, same split
# sync_recent_weather() already keeps around fetch_historical_hourly().
#
# `days` is Open-Meteo's forecast_days, capped at 16 and COUNTING TODAY -
# so days=16 returns today plus only 15 future days. Callers wanting N
# days ahead must ask for N+1 and must not assume the last requested day
# came back; routers/risk.py keeps that distinction explicit as
# FORECAST_API_DAYS vs FORECAST_HORIZON_DAYS.
def fetch_forecast_hourly(lat: float, lon: float, days: int = 16, timeout: int = 30) -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&forecast_days={days}"
        f"&hourly={HOURLY_FIELDS}&timezone=auto"
    )
    return _get_json(url, timeout)


def parse_hourly_rows(data: dict, lat: float, lon: float) -> list:
    """Open-Meteo's hourly response -> plain dicts shaped like WeatherHistory
    columns (not ORM objects), so callers can choose wholesale-replace
    (the import script) or dedupe-and-append (sync_recent_weather).

    lat/lon are the coordinates the response was FETCHED for, stamped onto
    every row - they are not in the response body. Required rather than
    optional on purpose: every caller already has them in hand, and a
    defaulted None would put unattributable rows back in the table, which
    is the whole problem these columns exist to fix.
    """
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    def series(name):
        values = hourly.get(name, [])
        return values + [None] * (len(times) - len(values))

    temp = series("temperature_2m")
    humidity = series("relative_humidity_2m")
    dew_point = series("dew_point_2m")
    precipitation = series("precipitation")
    weather_code = series("weather_code")
    wind_speed = series("wind_speed_10m")
    soil_temp = series("soil_temperature_6cm")
    uv_index = series("uv_index")
    sunshine = series("sunshine_duration")

    rows = []
    for i, t in enumerate(times):
        code = weather_code[i]
        rows.append({
            "timestamp": datetime.fromisoformat(t),
            "temp_c": temp[i],
            "humidity_pct": humidity[i],
            "dew_point_c": dew_point[i],
            "precipitation_mm": precipitation[i],
            "weather_code": int(code) if code is not None else None,
            "condition": _WMO_CONDITION.get(int(code), "Cloudy") if code is not None else "",
            "wind_speed_kmh": wind_speed[i],
            "soil_temp_6cm_c": soil_temp[i],
            "uv_index": uv_index[i],
            "sunshine_duration_s": sunshine[i],
            "lat": lat,
            "lon": lon,
        })
    return rows


def fetch_hourly_range(lat: float, lon: float, start: date, end: date) -> list:
    """Every hour between two dates, whichever era they fall in.

    Two Open-Meteo APIs cover this table and neither covers all of it: the
    historical-forecast one refuses any start_date before 2016, and the
    reanalysis archive carries neither soil temperature nor UV at any date.
    HISTORY_START_DATE is where this app switches between them, so a range
    that straddles it is fetched in two halves and the older half in
    ARCHIVE_CHUNK_YEARS-year chunks.

    The split is exactly the one scripts/import_historical_weather.py and
    scripts/import_historical_weather_archive.py already draw between
    themselves - so a range fetched here and a range fetched by those
    scripts produce identical rows, and the two can still be re-run over
    each other in any order.
    """
    boundary = date.fromisoformat(HISTORY_START_DATE)
    rows = []
    if start < boundary:
        for chunk_start, chunk_end in chunk_date_range(
                start, min(end, boundary - timedelta(days=1)), ARCHIVE_CHUNK_YEARS):
            rows += parse_hourly_rows(
                fetch_archive_hourly(lat, lon, chunk_start.isoformat(), chunk_end.isoformat()),
                lat, lon)
    if end >= boundary:
        rows += parse_hourly_rows(
            fetch_historical_hourly(lat, lon, max(start, boundary).isoformat(), end.isoformat()),
            lat, lon)
    return rows


# Catch-up is fetched in slices of at most this many days, each on the
# short per-request timeout below, and at most SYNC_MAX_CHUNKS_PER_CALL
# slices per tab open. A server that has been off (or offline) for months
# used to ask for the whole gap in ONE 3-second call, which never came back
# in time - so every later Weather/Risk load timed out the same way and the
# gap never closed. Slices land one at a time, so each tab open makes real
# progress and a long gap closes over a few opens.
SYNC_CHUNK_DAYS = 31
SYNC_MAX_CHUNKS_PER_CALL = 3
SYNC_FETCH_TIMEOUT = 3
# A failed catch-up isn't retried for this long. Without it an offline
# server paid the full fetch timeout on every single tab open.
SYNC_FAILURE_BACKOFF_SECONDS = 60
_sync_failed_until = 0.0


def sync_recent_weather(owner: Session, boord: Session) -> dict:
    """Best-effort catch-up: fetches whatever hours are missing since the
    last stored row and appends them (never replaces). Called as a side
    effect of loading the Weather tab, so a network hiccup here must never
    stop the tab from rendering whatever history is already stored - same
    "never block the caller" tone as fetch_weather() above.

    Reads/writes WeatherHistory on `owner`; reads the farm location on
    `boord`. The append is serialised on db.weather_append_lock so two
    simultaneous tab-opens don't both insert the same hour and collide on
    the timestamp unique index.

    Data is hourly, so once the latest stored row already falls in the
    current hour there is nothing new to fetch - that's the whole throttle,
    no extra cache/state needed to stop repeat tab-opens hammering the API.

    Uses a short timeout, not fetch_historical_hourly()'s 120s default: this
    runs synchronously inside the Weather/Risk tab's request, which the
    frontend abandons after Boord.NETWORK_TIMEOUT_MS (8s, see shared/api.js) -
    a slow/dead connection must fail fast enough here that the endpoint can
    still return the already-stored data within that budget, rather than
    the tab hanging past it and reading as fully offline.

    Returns {"synced": n} plus "error": True if a fetch failed (whatever
    landed before it is kept), "complete": False if more remains to catch
    up than one call fetches (see SYNC_MAX_CHUNKS_PER_CALL), and
    "no_location"/"location_changed" as below."""
    global _sync_failed_until
    if _time.monotonic() < _sync_failed_until:
        return {"synced": 0, "error": True}
    try:
        # .limit(1) is load-bearing, not tidiness. `.first()` reads the first
        # row off the cursor but leaves the SQL unbounded, so SQLite was told
        # to produce every WeatherHistory row - all columns, sorted by
        # timestamp descending - and then handed back one. On this farm's
        # 347,760 rows that measured 4.5-7.9 seconds for a single row, and it
        # was ~90% of everything the Weather tab waited for. The index on
        # timestamp was there the whole time and the query could not use it.
        # With a LIMIT, SQLite walks the index backwards and stops at the
        # first row.
        latest = owner.exec(
            select(WeatherHistory).order_by(WeatherHistory.timestamp.desc()).limit(1)
        ).first()
        now = datetime.now()
        if latest and latest.timestamp >= now.replace(minute=0, second=0, microsecond=0):
            return {"synced": 0}

        # Released before the fetch below - see farm_coords_and_release().
        coords = farm_coords_and_release(boord)
        if coords is None:
            # No location set: append nothing rather than guess. Callers
            # surface this as "set your farm location", not as an error.
            return {"synced": 0, "no_location": True}
        lat, lon = coords

        if latest is not None and not at_location(latest.lat, latest.lon, lat, lon):
            # The stored history was fetched somewhere else - the farm has
            # corrected its GPS since, or these rows predate the location
            # columns on a database that had none. Appending to it would
            # interleave two places' weather hour by hour, which is worse
            # than the gap: nothing downstream could tell them apart
            # afterwards. Say so, and leave it to the backfill to replace
            # the lot wholesale.
            return {"synced": 0, "location_changed": True}

        chunk_start = latest.timestamp.date() if latest else date.fromisoformat(HISTORY_START_DATE)
        today = now.date()
        newest = latest.timestamp if latest else None
        synced = 0
        for _ in range(SYNC_MAX_CHUNKS_PER_CALL):
            if chunk_start > today:
                break
            chunk_end = min(chunk_start + timedelta(days=SYNC_CHUNK_DAYS - 1), today)
            try:
                data = fetch_historical_hourly(lat, lon, chunk_start.isoformat(), chunk_end.isoformat(),
                                               timeout=SYNC_FETCH_TIMEOUT)
            except Exception:
                _sync_failed_until = _time.monotonic() + SYNC_FAILURE_BACKOFF_SECONDS
                return {"synced": synced, "error": True}
            new_rows = [WeatherHistory(**r) for r in parse_hourly_rows(data, lat, lon)
                        if newest is None or r["timestamp"] > newest]
            if new_rows:
                with db.weather_append_lock:
                    owner.add_all(new_rows)
                    owner.commit()
                synced += len(new_rows)
                newest = max(r.timestamp for r in new_rows)
            chunk_start = chunk_end + timedelta(days=1)
        result = {"synced": synced}
        if chunk_start <= today:
            result["complete"] = False
        return result
    except Exception:
        owner.rollback()
        _sync_failed_until = _time.monotonic() + SYNC_FAILURE_BACKOFF_SECONDS
        return {"synced": 0, "error": True}
