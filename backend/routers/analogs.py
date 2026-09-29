"""Similar past seasons for the Estimate tab: the seasons whose weather so far
came closest to the season being estimated, and what each of them produced.

The farmer's own way of estimating includes a judgement like "this looks
like a late-start, short year". This puts numbers under that judgement
without pretending to be a forecast. It ranks past seasons by how close
their weather was on the four factors the Risk tab scores (risk.DRIVERS,
from the one-off correlation study described in risk.build_risk_summary),
compared over the SAME calendar days - a factor whose window is still open
this season is compared only up to the last day of weather on file, in
every season alike. It then shows each close season's crop against its own
record's average, the spread of those, and (per block) the kg per tree those
seasons gave, as one more starting point for the estimate's Fill.

What it deliberately does NOT do:

  * Turn the similar seasons into a single kg figure. Weather explains only
    about a quarter to a half of this farm's season-to-season swing (see
    the Risk tab's methodology), and a handful of neighbours on one or two
    factors cannot do better than that. The Risk tab's Harvest Forecast is
    the one weather-driven kg figure, and it is shown beside the estimate
    already (see estimate-tab.js).
  * Use last season's crop: this farm's record shows no alternate-bearing
    pattern (r = 0.07 over 35 season pairs - risk.build_risk_summary).
  * Score picking start or season length. Neither is known until the
    season is under way or over, and with daily records for only a handful
    of seasons they would make distances incomparable between seasons that
    have them and seasons that don't. They are shown per season as
    descriptors instead.
  * Fetch anything. No weather sync, no forecast, no station reading - the
    endpoint reads what owner.db already holds, so it can neither hang on a
    provider nor hold Boord's database open across a network call (Boord is
    released as soon as its figures are read).

Seasons from the old orchard (1987-2009, whole-farm totals only - see
HistoricalAnnualYield) take part, but only as a percentage against their
own era's average and never in the per-block figures: their trees are gone.
Per-block seasons before risk.REGRESSION_START_YEAR are left out, as the
Risk tab's kg line leaves them out: the replanted orchard was still coming
into bearing, so a small crop then says nothing about the weather.
"""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from db import get_boord_session, get_owner_session
from models_owner import WeatherHistory
from routers.analysis import _block_sort_key, block_season_kg, season_day_kg
from routers.estimate import _check_season, _farm_totals
from routers.risk import DRIVERS, REGRESSION_START_YEAR, _date_range_rows, _driver_value, _window_dates
from weather import cached_history_stat, farm_coords

router = APIRouter(prefix="/api/estimate", tags=["estimate"])

# Method settings, not farm figures.
ANALOG_COUNT = 5        # at most this many similar seasons shown
MIN_ANALOGS = 2         # ...and at least this many, however few candidates
BLOCK_SEASONS = 3       # closest per-block seasons behind the block Fill figures
MIN_PARTIAL_DAYS = 14   # an open window is compared once this many days are in -
                        # and ranking starts as soon as one factor has them
MIN_COVERAGE = 0.9      # share of a window's days that must have the factor's data
MIN_CANDIDATES = 3      # fewer comparable seasons than this and there's no ranking
START_SHARE = 0.02      # "picking really started": 2% of that season's own total
CLOSE, FAIR = 0.5, 1.0  # distance bands, in spreads (standard deviations)
WEIGHT_FLOOR = 0.1      # closeness weight is 1 / max(distance, this)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _r(x, nd=1):
    return round(x, nd) if x is not None else None


def _season_timing(day_kg: dict, year: int) -> Optional[dict]:
    """First pick, when picking really got going (START_SHARE of the season's
    own total - relative to its own size, so a small crop doesn't read as a
    late one), last pick, and the span between. Daily-tracked seasons only."""
    per_day: dict = {}
    for (y, _, d), kg in day_kg.items():
        if y == year and kg > 0:
            per_day[d] = per_day.get(d, 0.0) + kg
    if not per_day:
        return None
    days = sorted(per_day)
    total = sum(per_day.values())
    cum, start = 0.0, days[-1]
    for d in days:
        cum += per_day[d]
        if cum >= START_SHARE * total:
            start = d
            break
    return {"first_date": days[0].isoformat(), "start_date": start.isoformat(),
            "last_date": days[-1].isoformat(), "span_days": (days[-1] - start).days + 1}


def _weather_values(owner: Session, spec: tuple, first_year: int, last_year: int) -> dict:
    """{year: {factor_key: value or None}} for every year with weather, each
    factor over the same month-day range in every year (spec: (key, start
    month, start day, end month, end day) per factor). None where fewer than
    MIN_COVERAGE of the range's days have that factor's field at all. A
    running total (sunshine hours, rain) over a range with a few days
    missing is scaled up to the whole range - otherwise a season with a gap
    reads as duller or drier than it was, while a mean is unaffected.

    One indexed range query per year, selecting only the factor columns - the
    whole record back to 1987 is ~40 years of hourly rows, most of them
    outside these windows."""
    drivers = {d["key"]: d for d in DRIVERS}
    cols = [getattr(WeatherHistory, f) for f in dict.fromkeys(drivers[k]["field"] for k, *_ in spec)]
    out: dict = {}
    for y in range(first_year, last_year + 1):
        starts = [date(y, sm, sd) for _, sm, sd, _, _ in spec]
        ends = [date(y, em, ed) for _, _, _, em, ed in spec]
        rows = owner.exec(select(WeatherHistory.timestamp, *cols).where(
            WeatherHistory.timestamp >= min(starts),
            WeatherHistory.timestamp < max(ends) + timedelta(days=1))).all()
        by_date: dict = {}
        for r in rows:
            by_date.setdefault(r.timestamp.date(), []).append(r)
        vals = {}
        for (key, _, _, _, _), start, end in zip(spec, starts, ends):
            d = drivers[key]
            window_rows = _date_range_rows(by_date, start, end)
            covered = {r.timestamp.date() for r in window_rows if getattr(r, d["field"]) is not None}
            days = (end - start).days + 1
            value = _driver_value(window_rows, d) if covered and len(covered) >= MIN_COVERAGE * days else None
            if value is not None and d["agg"] == "sum":
                value *= days / len(covered)
            vals[key] = value
        out[y] = vals
    return out


def build_analogs(boord: Session, owner: Session, season: Optional[int], today: date) -> dict:
    s = season_day_kg(boord, owner)
    current_year = s["current_year"]
    target = season if season is not None else current_year
    _check_season(target)
    block_year_kg, _ = block_season_kg(owner, s)
    totals, per_block_years = _farm_totals(owner, s)
    blocks = {bid: b.trees or 0 for bid, b in s["blocks"].items() if b.active or bid in block_year_kg}
    day_kg = s["day_kg"]
    no_location = farm_coords(boord) is None
    boord.close()   # everything below is owner.db only

    out = {"season_year": target, "current_year": current_year, "state": "ok",
           "no_location": no_location, "weather_through": None, "weather_behind": False,
           "cutoff": None, "first_window_opens": None, "factors": [], "analogs": [],
           "spread": None, "excluded": [], "target_timing": None,
           "block_basis_years": [], "blocks": {}}
    if target == current_year:
        t = _season_timing(day_kg, target)
        out["target_timing"] = {"first_date": t["first_date"]} if t else None

    last_ts = owner.exec(select(func.max(WeatherHistory.timestamp))).one()
    first_ts = owner.exec(select(func.min(WeatherHistory.timestamp))).one()
    if last_ts is None:
        out["state"] = "no_weather"
        return out
    # The last day with all 24 hours in, and never today, which is still
    # happening: a factor is compared on whole days only.
    last_complete = last_ts.date() if last_ts.hour == 23 else last_ts.date() - timedelta(days=1)
    cutoff = min(today - timedelta(days=1), last_complete)
    out["weather_through"] = last_complete.isoformat()
    out["weather_behind"] = (today - timedelta(days=1) - last_complete).days > 2
    out["cutoff"] = cutoff.isoformat()

    # --- Which factors, over which days ----------------------------------
    # "Pending" is the calendar's call (the window hasn't opened yet); a
    # window that HAS opened but has no weather on file past its start is
    # "behind" - the Estimate tab never fetches weather, so a stale record
    # is normal here and must not read as "nothing to compare yet".
    yesterday = today - timedelta(days=1)
    factors = []
    for d in DRIVERS:
        ws, we = _window_dates(target, d["window_md"])
        window_days = (we - ws).days + 1
        f = {"key": d["key"], "label": d["label"], "window": d["window_label"], "unit": d["unit"],
             "window_days": window_days, "observed_days": 0, "weight": 0.0, "compared_until": None,
             "target_value": None, "sd": None, "_start": ws, "_end": None}
        if yesterday < ws:
            f["status"] = "pending"
        elif cutoff < ws:
            f["status"] = "behind"
        else:
            end = min(we, cutoff)
            observed = (end - ws).days + 1
            f.update(observed_days=observed, _end=end, compared_until=f"{end:%d %b}")
            if end >= we:
                f.update(status="final", weight=1.0)
            elif observed >= MIN_PARTIAL_DAYS:
                f.update(status="partial", weight=round(observed / window_days, 3))
            else:
                f["status"] = "too_short"
        factors.append(f)
    out["first_window_opens"] = min(f["_start"] for f in factors).isoformat()

    def finish(state):
        out["state"] = state
        out["factors"] = [{k: v for k, v in f.items() if not k.startswith("_")} for f in factors]
        return out

    included = [f for f in factors if f["weight"] > 0]
    if not included:
        if all(f["status"] == "pending" for f in factors):
            return finish("no_weather_yet")
        if any(f["status"] == "behind" for f in factors):
            return finish("weather_behind")
        return finish("too_early")

    # --- Weather values, every year on file -------------------------------
    spec = tuple((f["key"], f["_start"].month, f["_start"].day, f["_end"].month, f["_end"].day)
                 for f in included)
    first_year, last_year = first_ts.year, max(last_ts.year, target)
    values = cached_history_stat(owner, ("estimate_analogs", spec, first_year, last_year),
                                 lambda: _weather_values(owner, spec, first_year, last_year))
    target_vals = values.get(target, {})
    for f in included:
        f["target_value"] = _r(target_vals.get(f["key"]), 2)
        if f["target_value"] is None:
            f.update(status="no_data", weight=0.0)
    included = [f for f in included if f["weight"] > 0]
    if not included:
        return finish("no_weather")

    # --- Candidate seasons -------------------------------------------------
    pool = {}
    for y, kg in totals.items():
        if y >= current_year or y == target or not kg or kg <= 0:
            continue
        record = "per_block" if y in per_block_years else "whole_farm"
        if record == "per_block" and y < REGRESSION_START_YEAR:
            out["excluded"].append({"year": y, "reason": "young_orchard"})
            continue
        pool[y] = {"kg": kg, "record": record}
    group_mean = {rec: _mean([p["kg"] for p in pool.values() if p["record"] == rec])
                  for rec in ("per_block", "whole_farm")}

    candidates = []
    for y in sorted(pool):
        vals = values.get(y)
        if not vals or all(v is None for v in vals.values()):
            out["excluded"].append({"year": y, "reason": "no_weather"})
        elif any(vals.get(f["key"]) is None for f in included):
            out["excluded"].append({"year": y, "reason": "weather_incomplete"})
        else:
            candidates.append(y)

    if len(candidates) < MIN_CANDIDATES:
        return finish("too_few_seasons")

    # Each factor's spread over the candidates: how far apart seasons
    # usually are on it, so one unit of distance means the same on all four.
    for f in included:
        xs = [values[y][f["key"]] for y in candidates]
        m = _mean(xs)
        sd = (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5
        f["sd"] = _r(sd, 3) if sd else None
        if not sd:
            f.update(status="no_spread", weight=0.0)
    included = [f for f in included if f["weight"] > 0]
    total_weight = sum(f["weight"] for f in included)
    if not included:
        return finish("no_spread")

    # --- Distance and ranking ---------------------------------------------
    ranked = []
    for y in candidates:
        zs = {f["key"]: (values[y][f["key"]] - values[target][f["key"]]) / f["sd"] for f in included}
        dist = (sum(f["weight"] * zs[f["key"]] ** 2 for f in included) / total_weight) ** 0.5
        ranked.append((dist, y, zs))
    ranked.sort(key=lambda t: (t[0], -t[1]))   # ties: the newer season first

    top_n = min(ANALOG_COUNT, max(MIN_ANALOGS, len(ranked) // 2))
    top = ranked[:top_n]
    inv = {y: 1 / max(dist, WEIGHT_FLOOR) for dist, y, _ in top}
    inv_total = sum(inv.values())

    # Per-block kg/tree from the closest per-block seasons - which may reach
    # past the top few when those are old-orchard seasons.
    block_basis = [(dist, y) for dist, y, _ in ranked if pool[y]["record"] == "per_block"][:BLOCK_SEASONS]
    block_basis_years = [y for _, y in block_basis]
    out["block_basis_years"] = sorted(block_basis_years)
    for bid in sorted(blocks, key=_block_sort_key):
        trees = blocks[bid]
        pts = [(1 / max(dist, WEIGHT_FLOOR), block_year_kg.get(bid, {}).get(y, 0.0) / trees)
               for dist, y in block_basis if trees and block_year_kg.get(bid, {}).get(y, 0.0) > 0]
        wsum = sum(w for w, _ in pts)
        out["blocks"][bid] = round(sum(w * v for w, v in pts) / wsum, 1) if wsum else None

    shown = top + [r for r in ranked[top_n:] if r[1] in block_basis_years]
    top_years = {y for _, y, _ in top}
    for dist, y, zs in shown:
        p = pool[y]
        mean = group_mean[p["record"]]
        out["analogs"].append({
            "year": y, "record": p["record"], "distance": round(dist, 2),
            "closeness": "close" if dist < CLOSE else "fair" if dist < FAIR else "loose",
            "weight": round(inv[y] / inv_total, 3) if y in top_years else None,
            "in_top": y in top_years,
            "kg": _r(p["kg"]),
            "vs_avg_pct": round((p["kg"] / mean - 1) * 100, 1) if mean else None,
            "timing": _season_timing(day_kg, y),
            "factors": {k: {"value": _r(values[y][k], 2), "z": round(z, 2)} for k, z in zs.items()},
        })
    pcts = [a["vs_avg_pct"] for a in out["analogs"] if a["in_top"] and a["vs_avg_pct"] is not None]
    out["spread"] = {"min_pct": min(pcts), "max_pct": max(pcts), "n": len(pcts)} if pcts else None
    return finish("ok")


@router.get("/analogs")
def estimate_analogs(season: Optional[int] = None,
                     boord: Session = Depends(get_boord_session),
                     owner: Session = Depends(get_owner_session)):
    """Similar past seasons for the Estimate tab - see the module docstring."""
    return build_analogs(boord, owner, season, date.today())
