"""Ask about this weather: the summary the model is given for the Weather tab.

The Estimate tab's Ask (routers/ai.py) is the template: the server works the
figures out, the model only explains them. Here the figures are the farm's
own weather record - the years and measurements ticked on the tab, the
record's year-by-year values for those measurements, the last week, and the
forecast for the week ahead. One location only: the coordinates Boord's
Settings hold, which is also what the stored history was fetched for.

Nothing here writes anything, and Boord's database is let go (farm_coords_
and_release) before the forecast is fetched.
"""
import threading
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from models_owner import WeatherHistory
from routers.weather import _METRICS, _years_on_file, build_weather_history
from weather import (cached_history_stat, farm_coords_and_release, fetch_forecast_hourly,
                     fetch_weather_cached, parse_hourly_rows, _ttl_cached)

MAX_YEARS = 12            # selected years sent in full (daily detail is monthly here)
MAX_METRICS = 4
FORECAST_DAYS = 7         # days ahead, not counting today
FROST_C = 2.0             # a forecast night at or below this is flagged
HEAT_C = 35.0             # a forecast day at or above this is flagged
WET_DAY_MM = 1.0          # a day with at least this much rain counts as a rain day
RECENT_DAYS = 7

_METRIC = {m["key"]: m for m in _METRICS}
_SUM_KEYS = {m["key"] for m in _METRICS if m["agg"] == "sum"}


WEATHER_SYSTEM_PROMPT = """You are a careful assistant helping a fruit farm's owner read the weather record and forecast for their farm.

You will receive a JSON summary of the app's Weather tab, for one location - the farm:
- context: today's date, how far the stored record runs, the years on file, and the units
- measurements: the ones the owner has ticked, each with how it is measured (mean, daily max, total...)
- selected_years: for each selected year and measurement - days on file, mean, lowest and highest day (with dates), a total for rain and sunshine, and the 12 monthly figures
- record: for each measurement, the figure for every year on file ("by_year") and the record average, so a year can be ranked against the rest. "same_period_by_year" covers only 1 January to the date the current year has reached, so an unfinished year is compared like with like
- last_7_days: the farm's most recent daily figures
- forecast: today and the next days, with highlights for frost, heat and rain
- current: conditions at the farm now, if known
- highlights: notable facts already worked out from the figures above

Rules:
- Use ONLY the figures in the JSON. Never invent or estimate a missing figure; say it is not on file.
- Name years and months exactly as the JSON does. Always give units (°C, mm, %, km/h, hrs).
- The current year is unfinished: compare it with other years using same_period_by_year, not by_year.
- Weather before 2020 has no soil temperature or UV index. A null is "not recorded", not zero.
- The forecast is a model's guess, less reliable beyond about three days: say "forecast", never state it as fact. The history is Open-Meteo's modelled grid for the farm's location, not the farm's own station.
- You may relate the weather to orchard concerns (frost, heat stress, rain around flowering or picking) but say plainly that this is general knowledge, not something in the figures, and do not predict harvest size.
- Be concise: short paragraphs or bullet points, most important first. No JSON or code."""

WEATHER_USER_TEMPLATE = """Here are the Weather tab's figures:

{summary}

Question: {question}

Answer from the figures above only."""


def _r(v, d=1):
    return None if v is None else round(v, d)


# --------------------------------------------------------------------------- #
# The record, year by year
# --------------------------------------------------------------------------- #
def _record_by_year(owner: Session, key: str, cutoff: Optional[str] = None) -> dict:
    """{year: figure} for every year on file: the year's total for rain and
    sunshine, else the mean of its daily values (daily max/min for the max/
    min measurements - "how hot do the days usually get"). `cutoff` ("MM-DD")
    limits every year to 1 January through that day. Aggregated in SQL and
    cached until WeatherHistory next changes, like the Weather tab's years list."""
    m = _METRIC[key]

    def compute():
        col = getattr(WeatherHistory, m["source"])
        agg = {"mean": func.avg, "sum": func.sum, "max": func.max, "min": func.min}[m["agg"]]
        day = func.date(WeatherHistory.timestamp).label("day")
        q = select(day, agg(col).label("v"))
        if cutoff:
            q = q.where(func.strftime("%m-%d", WeatherHistory.timestamp) <= cutoff)
        daily = q.group_by(day).subquery()
        year = func.substr(daily.c.day, 1, 4)
        roll = func.sum if key in _SUM_KEYS else func.avg
        rows = owner.exec(select(year, roll(daily.c.v), func.count(daily.c.v))
                          .where(daily.c.v.is_not(None)).group_by(year).order_by(year)).all()
        scale = m.get("scale", 1)
        return {int(y): _r(v * scale, m["decimals"] + (1 if key in _SUM_KEYS else 0)) for y, v, n in rows}
    return dict(cached_history_stat(owner, ("ai_record", key, cutoff), compute))


def _record_block(owner: Session, key: str, current_year: int, cutoff: Optional[str]) -> dict:
    m = _METRIC[key]
    by_year = _record_by_year(owner, key)
    # Finished years only: the current year is part of a different comparison.
    finished = {y: v for y, v in by_year.items() if y < current_year}
    out = {
        "what_is_compared": ("each year's total" if key in _SUM_KEYS else "mean of each year's daily values"),
        "unit": m["unit"],
        "by_year": finished,
        "average": _r(sum(finished.values()) / len(finished), 2) if finished else None,
    }
    if cutoff and current_year in by_year:
        to_date = {y: v for y, v in _record_by_year(owner, key, cutoff).items()}
        past = {y: v for y, v in to_date.items() if y < current_year}
        out["same_period"] = f"1 Jan to {cutoff[3:]}/{cutoff[:2]} (day/month) of every year"
        out["same_period_by_year"] = to_date
        out["same_period_average"] = _r(sum(past.values()) / len(past), 2) if past else None
    return out


# --------------------------------------------------------------------------- #
# The selected years
# --------------------------------------------------------------------------- #
def _year_stats(points: list, key: str) -> dict:
    m = _METRIC[key]
    have = [p for p in points if p.get(key) is not None]
    if not have:
        return {"days": 0}
    vals = [p[key] for p in have]
    lo = min(have, key=lambda p: p[key])
    hi = max(have, key=lambda p: p[key])
    months = defaultdict(list)
    for p in have:
        months[int(p["date"][5:7])].append(p[key])
    roll = (lambda v: sum(v)) if key in _SUM_KEYS else (lambda v: sum(v) / len(v))
    out = {
        "days": len(have),
        "first_day": have[0]["date"], "last_day": have[-1]["date"],
        "mean_of_days": _r(sum(vals) / len(vals), m["decimals"] + 1),
        "lowest_day": {"date": lo["date"], "value": lo[key]},
        "highest_day": {"date": hi["date"], "value": hi[key]},
        "by_month": {str(mo): _r(roll(v), m["decimals"] + (1 if key in _SUM_KEYS else 0))
                     for mo, v in sorted(months.items())},
    }
    if key in _SUM_KEYS:
        out["total"] = _r(sum(vals), m["decimals"])
        if key == "precipitation_mm":
            out["days_with_1mm_or_more"] = sum(1 for v in vals if v >= WET_DAY_MM)
    return out


# --------------------------------------------------------------------------- #
# The forecast
# --------------------------------------------------------------------------- #
_forecast_cache: dict = {}
_forecast_lock = threading.Lock()


def _fetch_forecast(lat: float, lon: float) -> list:
    """Daily rows for today and the days ahead, or [] when the weather service
    can't be reached (cached briefly either way - see weather._ttl_cached)."""
    def fetch():
        try:
            data = fetch_forecast_hourly(lat, lon, days=FORECAST_DAYS + 1, timeout=15)
        except Exception as e:  # noqa: BLE001 - the forecast is one part of many
            print(f"[ai] forecast unavailable: {type(e).__name__}", flush=True)
            return []
        return parse_hourly_rows(data, lat, lon)
    return _ttl_cached(_forecast_cache, _forecast_lock, (round(lat, 4), round(lon, 4)), fetch)


def _forecast_days(rows: list) -> list:
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["timestamp"].date()].append(r)

    def col(rs, name):
        return [r[name] for r in rs if r[name] is not None]

    out = []
    for d, rs in sorted(by_day.items()):
        t, rain, wind, hum = col(rs, "temp_c"), col(rs, "precipitation_mm"), col(rs, "wind_speed_kmh"), col(rs, "humidity_pct")
        noon = next((r for r in rs if r["timestamp"].hour == 12 and r["condition"]), None)
        out.append({
            "date": d.isoformat(),
            "temp_max_c": _r(max(t)) if t else None,
            "temp_min_c": _r(min(t)) if t else None,
            "rain_mm": _r(sum(rain)) if rain else None,
            "wind_mean_kmh": _r(sum(wind) / len(wind)) if wind else None,
            "wind_peak_hourly_kmh": _r(max(wind)) if wind else None,
            "humidity_mean_pct": _r(sum(hum) / len(hum), 0) if hum else None,
            "conditions": noon["condition"] if noon else None,
        })
    return out


def _forecast_block(lat: float, lon: float, today: date) -> Optional[dict]:
    days = [d for d in _forecast_days(_fetch_forecast(lat, lon)) if d["date"] >= today.isoformat()]
    if not days:
        return None
    frost = [d["date"] for d in days if d["temp_min_c"] is not None and d["temp_min_c"] <= FROST_C]
    heat = [d["date"] for d in days if d["temp_max_c"] is not None and d["temp_max_c"] >= HEAT_C]
    wet = [d for d in days if d["rain_mm"] is not None and d["rain_mm"] >= WET_DAY_MM]
    return {
        "note": "Open-Meteo model forecast, hourly figures reduced to days; less reliable beyond about three days",
        "days": days,
        "frost_nights_at_or_below_c": {"threshold": FROST_C, "dates": frost},
        "hot_days_at_or_above_c": {"threshold": HEAT_C, "dates": heat},
        "rain_days_1mm_or_more": [d["date"] for d in wet],
        "rain_total_mm": _r(sum(d["rain_mm"] or 0 for d in days)),
    }


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #
def _highlights(selected: dict, record: dict, current_year: int, forecast: Optional[dict],
                last_synced: Optional[str]) -> list:
    out = []
    for key, rec in record.items():
        m = _METRIC[key]
        for year in selected:
            if year == current_year and rec.get("same_period_by_year") and rec.get("same_period_average") is not None:
                series, avg, label = rec["same_period_by_year"], rec["same_period_average"], "so far this year"
            elif year in rec["by_year"] and rec["average"] is not None:
                series, avg, label = rec["by_year"], rec["average"], "for the year"
            else:
                continue
            v = series.get(year)
            if v is None:
                continue
            ranked = sorted((val for y, val in series.items() if val is not None), reverse=True)
            rank = ranked.index(v) + 1
            diff = v - avg
            pct = f" ({diff / avg * 100:+.0f}%)" if avg else ""
            out.append(f"{m['label']} {label} {year}: {v} {m['unit']} against a record average of {avg} {m['unit']}"
                       f"{pct}; ranks {rank} of {len(ranked)} years on file (1 = highest).")
    if forecast:
        if forecast["frost_nights_at_or_below_c"]["dates"]:
            out.append("Forecast nights at or below %.0f °C: %s." % (
                FROST_C, ", ".join(forecast["frost_nights_at_or_below_c"]["dates"])))
        if forecast["hot_days_at_or_above_c"]["dates"]:
            out.append("Forecast days at or above %.0f °C: %s." % (
                HEAT_C, ", ".join(forecast["hot_days_at_or_above_c"]["dates"])))
        out.append(f"Forecast rain over the next {len(forecast['days'])} days (today included): "
                   f"{forecast['rain_total_mm']} mm on {len(forecast['rain_days_1mm_or_more'])} day(s).")
    if last_synced:
        out.append(f"Stored weather runs to {last_synced.replace('T', ' ')[:16]}.")
    return out


def build_weather_summary(owner: Session, boord: Session, years: list, metrics: list, today: date) -> dict:
    """Everything the model is told, as JSON-ready data, plus "_check" - the
    years it may fairly name (the browser flags an answer naming any other)."""
    coords = farm_coords_and_release(boord)   # Boord is let go before any network call
    all_years = _years_on_file(owner)
    current_year = today.year

    keys = [k for k in dict.fromkeys(metrics) if k in _METRIC][:MAX_METRICS] or ["temp_c"]
    on_file = set(all_years)
    chosen = sorted({y for y in years if y in on_file})[-MAX_YEARS:] or all_years[-1:]

    history = build_weather_history(owner, boord, years=chosen) if chosen else {"points": [], "last_synced": None}
    by_year = defaultdict(list)
    for p in history["points"]:
        by_year[p["year"]].append(p)
    last_synced = history.get("last_synced")
    # Same-period comparisons stop where the newest stored hour does.
    cutoff = last_synced[5:10] if last_synced and last_synced[:4] == str(current_year) else None

    selected = {y: {k: _year_stats(by_year.get(y, []), k) for k in keys} for y in chosen}
    record = {k: _record_block(owner, k, current_year, cutoff) for k in keys} if all_years else {}

    recent_src = by_year.get(current_year) or (by_year[max(by_year)] if by_year else [])
    recent_src = [p for p in recent_src if last_synced and p["date"] <= last_synced[:10]]
    recent = [{"date": p["date"], **{k: p[k] for k in keys}} for p in recent_src[-RECENT_DAYS:]]

    forecast = current = None
    if coords is not None:
        forecast = _forecast_block(*coords, today)
        try:
            cur = fetch_weather_cached(*coords)
        except Exception:  # noqa: BLE001
            cur = {}
        current = {k: cur.get(k) for k in ("temp", "humidity", "condition") if cur.get(k) is not None} or None

    summary = {
        "context": {
            "today": today.isoformat(),
            "location": "the farm (Boord's GPS setting)" if coords is not None else "not set - no forecast",
            "years_on_file": f"{all_years[0]}-{all_years[-1]}" if all_years else None,
            "stored_weather_runs_to": last_synced,
            "current_year_is_unfinished": current_year in on_file,
            "units": "temperatures in °C, rain in mm, wind in km/h, humidity in %, sunshine in hours",
        },
        "measurements": [{"key": k, "label": _METRIC[k]["label"], "unit": _METRIC[k]["unit"],
                          "daily_value_is": {"mean": "the day's mean", "sum": "the day's total",
                                             "max": "the day's highest", "min": "the day's lowest"}[_METRIC[k]["agg"]]}
                         for k in keys],
        "selected_years": {str(y): v for y, v in selected.items()},
        "record": record,
        "last_7_days": recent,
        "forecast": forecast,
        "current": current,
    }
    summary["highlights"] = _highlights(selected, record, current_year, forecast, last_synced)
    summary["_check"] = {"years": sorted({*all_years, current_year})}
    return summary
