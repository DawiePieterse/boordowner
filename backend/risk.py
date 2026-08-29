from collections import defaultdict
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from db import get_session
from models import HistoricalAnnualYield, SystemSetting, WeatherHistory
from routers.analysis import build_analysis_summary
from security import get_current_admin
from weather import farm_coords, fetch_forecast_hourly, parse_hourly_rows, sync_recent_weather

router = APIRouter(prefix="/api/risk", tags=["risk"])

# ---------------------------------------------------------------------------
# Critical Season Risk Indicator: a transparent 0-100 score built from the
# weather drivers that best explained this farm's own harvest variation -
# NOT a generic agronomy model. See build_risk_summary()'s docstring for
# the correlation study behind this list and its caveats.
#
# Each driver is measured over a fixed calendar window (same every year,
# regardless of when picking actually starts) and contributes up to 25
# risk points, scaled between the best and worst value seen across the
# REFERENCE_START_YEAR-onward seasons - so "25/25" means "as bad as the
# worst season on file for this factor", not an absolute agronomic
# threshold.
#
# build_harvest_forecast() (further below) reuses this exact same driver
# list and scoring to turn the current season's still-open windows into
# three kg predictions (Favorable/Expected/Unfavorable) instead of a single
# risk score - see that function's docstring for how.
# ---------------------------------------------------------------------------
DRIVERS = [
    {
        "key": "spring_dryness", "label": "Fruit Development Air Dryness",
        "window_md": ((9, 16), (10, 31)), "window_label": "16 Sep - 31 Oct",
        "field": "dew_point_c", "agg": "mean",
        "unit": "°C mean dew point", "direction": "lower_is_worse",
        "why": "The single strongest weather signal in this farm's whole record: "
               "dry air while fruit is sizing means the crop loses water faster "
               "than it can take it up, and those seasons came in smaller. Dew "
               "point measures that dryness more directly than humidity alone.",
    },
    {
        "key": "fruit_warmth", "label": "Fruit Development Warmth",
        "window_md": ((9, 16), (11, 15)), "window_label": "16 Sep - 15 Nov",
        "field": "temp_c", "agg": "daily_max_mean",
        "unit": "°C mean daily maximum", "direction": "higher_is_worse",
        "why": "Persistently warm afternoons through fruit development line up "
               "with smaller crops here - a steadier measure than counting rare "
               "extreme days, since it picks up a hot spring overall rather than "
               "a handful of spikes.",
    },
    {
        "key": "flowering_sun", "label": "Flowering-Period Sunshine",
        "window_md": ((8, 1), (9, 15)), "window_label": "1 Aug - 15 Sep",
        "field": "sunshine_duration_s", "agg": "sum", "scale": 1 / 3600,
        "unit": "hours of sunshine", "direction": "lower_is_worse",
        "why": "Bright flowering weather produced the bigger crops in this "
               "farm's record; dull, overcast spells while the trees are in "
               "flower mean poorer pollination and fruit set.",
    },
    {
        "key": "sizing_rain", "label": "Fruit-Sizing Rainfall",
        "window_md": ((10, 1), (11, 30)), "window_label": "1 Oct - 30 Nov",
        "field": "precipitation_mm", "agg": "sum",
        "unit": "mm total rain", "direction": "lower_is_worse",
        "why": "Rain while the fruit is filling out fed this farm's bigger "
               "harvests; its dry Octobers and Novembers line up with the "
               "smaller ones.",
    },
]

# Aggregations whose value is an average per unit of time (so a partial
# window is blended by day-weight) rather than a running total that
# accumulates with window length (blended by rate) - see _project_driver().
_INTENSIVE_AGGS = {"mean", "daily_max_mean"}

# Seasons the score normalizes against. 2012 is the first season of the
# replanted orchard with per-block records (HistoricalAnnualYield); going
# further back would mix in the OLD orchard, grubbed and replanted around
# 2010, whose yields came off entirely different trees - see
# scripts/import_historical_annual_yield.py.
REFERENCE_START_YEAR = 2012

# The kg regression (Harvest Forecast only) needs a NARROWER window than the
# score's normalization range above, because it maps a weather score onto an
# absolute kg total and the orchard itself was still filling out. In
# 2012-2015 block 7 was the only block bearing at all - the replanted blocks
# contributed literally 0 kg until 2016 - so those seasons' small totals
# reflect an immature orchard, not bad weather. Fitting them would teach the
# line that a moderate risk score means a tiny crop. Weather-vs-weather
# normalization is unaffected by orchard age, hence the two different years.
REGRESSION_START_YEAR = 2016

_BANDS = [(25, "Low"), (50, "Moderate"), (75, "Elevated"), (101, "High")]

# Open-Meteo's forecast API tops out at forecast_days=16, and that count
# INCLUDES today - so asking for the maximum yields only 15 usable *future*
# days. These are deliberately two separate numbers: conflating them makes
# the last day of the forecast window silently contribute no data (it falls
# past the end of what the API actually returned) while still being counted
# as a forecast day. Anything in a driver's window beyond the horizon is
# blended in from the historical scenario range instead - see
# _segment_days().
FORECAST_API_DAYS = 16       # what we request (API maximum, counts today)
FORECAST_HORIZON_DAYS = 15   # usable future days that actually come back


def _band(score: float) -> str:
    for ceiling, name in _BANDS:
        if score < ceiling:
            return name
    return "High"


def _reference_label(years: list) -> str:
    """"2012-2025" (or "2019" for a lone season) - the reference range, for
    screens that name it in prose. Sent from here rather than hardcoded in
    the frontend so widening REFERENCE_START_YEAR can't leave the UI
    quoting a range the score no longer uses."""
    if not years:
        return ""
    return str(years[0]) if len(years) == 1 else f"{years[0]}-{years[-1]}"


def _window_dates(year: int, window_md) -> tuple:
    (sm, sd), (em, ed) = window_md
    return date(year, sm, sd), date(year, em, ed)


def _window_status(year: int, window_md, today: date) -> str:
    start, end = _window_dates(year, window_md)
    if today < start:
        return "pending"
    if today > end:
        return "final"
    return "in_progress"


def _date_range_rows(by_date: dict, start: date, end: date) -> list:
    rows = []
    d = start
    while d <= end:
        rows.extend(by_date.get(d, ()))
        d += timedelta(days=1)
    return rows


def _window_rows(by_date: dict, year: int, window_md, cutoff: date = None) -> list:
    start, end = _window_dates(year, window_md)
    if cutoff is not None and cutoff < end:
        end = cutoff
    return _date_range_rows(by_date, start, end)


def _driver_value(rows: list, driver: dict):
    """A driver's raw value over `rows`, or None where the underlying data
    is entirely missing. "scale" (optional) is applied after aggregating,
    to report a legible unit - e.g. sunshine is stored in seconds but read
    as hours."""
    field = driver["field"]
    agg = driver["agg"]
    scale = driver.get("scale", 1)

    def scaled(v):
        return None if v is None else v * scale

    if agg == "count_lt":
        vals = [v for v in (getattr(r, field) for r in rows) if v is not None]
        return scaled(float(sum(1 for v in vals if v < driver["threshold"]))) if vals else None
    if agg == "count_gt":
        vals = [v for v in (getattr(r, field) for r in rows) if v is not None]
        return scaled(float(sum(1 for v in vals if v > driver["threshold"]))) if vals else None
    if agg == "count_days_gt":
        days = {r.timestamp.date() for r in rows
                if getattr(r, field) is not None and getattr(r, field) > driver["threshold"]}
        any_data = any(getattr(r, field) is not None for r in rows)
        return scaled(float(len(days))) if any_data else None
    if agg == "mean":
        vals = [v for v in (getattr(r, field) for r in rows) if v is not None]
        return scaled(sum(vals) / len(vals)) if vals else None
    if agg == "sum":
        vals = [v for v in (getattr(r, field) for r in rows) if v is not None]
        return scaled(sum(vals)) if vals else None
    if agg == "daily_max_mean":
        # Mean of each day's own peak - "how hot did the afternoons get,
        # typically", which a flat hourly mean would wash out against the
        # cool nights in the same window.
        per_day = defaultdict(list)
        for r in rows:
            v = getattr(r, field)
            if v is not None:
                per_day[r.timestamp.date()].append(v)
        peaks = [max(v) for v in per_day.values()]
        return scaled(sum(peaks) / len(peaks)) if peaks else None
    raise ValueError(f"unknown agg {agg}")


def _risk_points(value, hist_values: list, direction: str):
    """0-25 risk points, scaled linearly between the best and worst value
    seen across the reference seasons. Clamped at both ends, so a
    value beyond any historical extreme still just reads as "as bad/good as
    the worst/best seen so far" rather than extrapolating past it."""
    if value is None or not hist_values:
        return None
    lo, hi = min(hist_values), max(hist_values)
    if hi == lo:
        return 12.5  # no variation across the reference seasons - neutral
    frac = (hi - value) / (hi - lo) if direction == "lower_is_worse" else (value - lo) / (hi - lo)
    return round(25 * max(0.0, min(1.0, frac)), 1)


def _compute_driver_state(session: Session) -> dict:
    """Shared setup behind both build_risk_summary() and
    build_harvest_forecast(): per-(driver,year) status/value against real
    WeatherHistory, and the reference range each driver is normalized
    against (REFERENCE_START_YEAR through last season). Pulled out as its
    own function so the two features can't silently drift apart the way
    duplicated windowing logic would risk - build_risk_summary() is a thin
    wrapper over this.

    Season totals come from two places, since the reference range now
    reaches back past the app's own daily records: build_analysis_summary()
    for the daily-tracked seasons (2020 on) and HistoricalAnnualYield for
    the annual-only ones before that (2012-2019)."""
    settings = session.exec(select(SystemSetting)).first()
    current_year = settings.current_harvest_year if settings else date.today().year

    analysis = build_analysis_summary(session)
    kg_by_year = {m["year"]: m["total_kg"] for m in analysis["monthly"]}
    annual_totals: dict = defaultdict(float)
    for a in session.exec(select(HistoricalAnnualYield)).all():
        annual_totals[a.season_year] += a.kg
    for year, kg in annual_totals.items():
        # setdefault, not +=: a season tracked day-by-day is already whole
        # in kg_by_year, and the annual table's own row for it (if one is
        # ever imported) would be the same harvest counted twice.
        kg_by_year.setdefault(year, kg)

    # Reference seasons: everything from REFERENCE_START_YEAR up to (not
    # including) the current one that we actually have a season total for.
    # Weather alone isn't enough - a season with no yield on file can't
    # anchor the score or the forecast regression.
    historical_years = sorted(y for y, kg in kg_by_year.items()
                              if REFERENCE_START_YEAR <= y < current_year and kg)
    all_years = sorted(set(historical_years) | {current_year})

    # Every DRIVERS window_md falls entirely within its own calendar year (no
    # Dec-into-Jan spillover), so a plain min(all_years) floor is exact - no
    # buffer needed. Bounding this matters because WeatherHistory reaches
    # back to 1987 (see scripts/import_historical_weather_archive.py) while
    # this function only ever looks up the reference range plus the current
    # season - without it, that's a 25-year pointless table scan.
    rows = session.exec(select(WeatherHistory).where(
        WeatherHistory.timestamp >= date(min(all_years), 1, 1))).all()
    by_date = defaultdict(list)
    for r in rows:
        by_date[r.timestamp.date()].append(r)

    today = date.today()

    # value/status per (driver, year)
    value_by_year = {d["key"]: {} for d in DRIVERS}
    status_by_year = {d["key"]: {} for d in DRIVERS}
    for year in all_years:
        for d in DRIVERS:
            status = _window_status(year, d["window_md"], today)
            status_by_year[d["key"]][year] = status
            if status == "pending":
                value_by_year[d["key"]][year] = None
                continue
            cutoff = today if status == "in_progress" else None
            w_rows = _window_rows(by_date, year, d["window_md"], cutoff)
            value_by_year[d["key"]][year] = _driver_value(w_rows, d)

    # Reference range per driver, for normalizing every season (including
    # the current one) on the same scale.
    hist_range = {
        d["key"]: [value_by_year[d["key"]][y] for y in historical_years
                    if value_by_year[d["key"]].get(y) is not None]
        for d in DRIVERS
    }

    return {
        "current_year": current_year, "historical_years": historical_years, "all_years": all_years,
        "kg_by_year": kg_by_year, "by_date": by_date, "today": today,
        "value_by_year": value_by_year, "status_by_year": status_by_year, "hist_range": hist_range,
    }


def build_risk_summary(session: Session) -> dict:
    """Critical Season Risk Indicator: a transparent 0-100 score of how this
    season's weather compares to the reference seasons (REFERENCE_START_YEAR
    onward) on the four factors (see DRIVERS above) that best correlated
    with this farm's own season-total kg.

    That correlation study is a one-off analysis behind this driver list,
    not something recomputed live - re-deriving "best" drivers from a live,
    growing sample would make the scoring formula itself drift season to
    season. It was last re-run against the farm's full 1987-2025 record
    (Pearson/Spearman over daily WeatherHistory aggregated into calendar
    windows) once that history was imported, testing ~230 driver x window
    combinations against three samples: block 7 alone 1987-2025 (n=37, the
    one block continuously in production across the whole record - the old
    workbook's "Blok 7" is today's block 7, same 2132 trees planted 1982),
    the old orchard's farm total 1987-2009 (n=23), and the replanted
    orchard's 2012-2025 (n=14). The four kept here are the ones that held
    the same sign across all three and stayed near-independent of each
    other (all pairwise |r| <= 0.37, so the score isn't counting one
    underlying signal four times - an easy trap here, since dew point,
    humidity, dry-hours and diurnal range all measure spring dryness and
    correlate 0.67-0.96 with each other).

    Caveats worth keeping in mind everywhere this score is shown. Weather
    explains roughly a quarter to a half of this farm's season-to-season
    variation (R^2 ~ 0.26 on block 7, ~0.46 on the modern farm total) - real
    signal, but most of a season's outcome is still down to things this
    score can't see. ~230 combinations were tested, so some apparent
    winners are chance; agreement across samples is the real filter, and
    those samples overlap (block 7 is ~18% of the old orchard's total but
    ~56% of the modern one), so they aren't three fully independent
    confirmations. Note also that this farm's record shows NO
    alternate-bearing pattern, contrary to what lychee growers often expect
    and to what an earlier version of this docstring claimed: across 35
    consecutive-season pairs, last season's crop predicts this one at
    r = 0.07 (p = 0.69) - statistically nothing.

    Each driver is scored per season (including in-progress current one) by
    _risk_points() against that reference range, and summed to 0-100.
    A component whose calendar window hasn't closed yet for the current
    season is left out of the sum (never assumed to be zero risk) - see
    "known_count" on each season entry.
    """
    state = _compute_driver_state(session)
    current_year = state["current_year"]
    all_years = state["all_years"]
    value_by_year = state["value_by_year"]
    status_by_year = state["status_by_year"]
    hist_range = state["hist_range"]

    drivers_out = [{
        "key": d["key"], "label": d["label"], "window": d["window_label"],
        "unit": d["unit"], "direction": d["direction"], "why": d["why"],
        "historical_min": round(min(hist_range[d["key"]]), 1) if hist_range[d["key"]] else None,
        "historical_max": round(max(hist_range[d["key"]]), 1) if hist_range[d["key"]] else None,
    } for d in DRIVERS]

    seasons = []
    for year in all_years:
        components = []
        known_sum = 0.0
        known_count = 0
        for d in DRIVERS:
            value = value_by_year[d["key"]][year]
            status = status_by_year[d["key"]][year]
            risk_points = None
            if status == "final" and value is not None:
                risk_points = _risk_points(value, hist_range[d["key"]], d["direction"])
                if risk_points is not None:
                    known_sum += risk_points
                    known_count += 1
            components.append({
                "key": d["key"], "status": status,
                "value": round(value, 1) if value is not None else None,
                "risk_points": risk_points,
            })
        risk_score = round(known_sum, 1) if known_count == len(DRIVERS) else None
        seasons.append({
            "year": year, "is_current": year == current_year,
            "total_kg": state["kg_by_year"].get(year, 0.0),
            "components": components,
            "known_count": known_count,
            "score_so_far": round(known_sum, 1) if known_count else None,
            "risk_score": risk_score,
            "band": _band(risk_score) if risk_score is not None else None,
        })

    return {
        "current_year": current_year,
        "historical_years": state["historical_years"],
        "reference_label": _reference_label(state["historical_years"]),
        "driver_count": len(DRIVERS),
        "drivers": drivers_out,
        "seasons": seasons,
    }


@router.get("/summary")
def risk_summary(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Risk tab data - see build_risk_summary(). No caller in Boord's own
    UI since the Risk tab left with the Owner View; kept as that app's API,
    same as analysis.py/weather.py. See owner-app/README.md."""
    return build_risk_summary(session)


# ---------------------------------------------------------------------------
# Harvest Forecast: three current-season kg predictions (Favorable/Expected/
# Unfavorable), built by projecting each still-open driver window forward
# and running the projection through the exact same _risk_points() scoring
# as the real Risk tab, then converting the resulting score to kg via a
# regression fit on the historical (risk_score, total_kg) pairs.
# ---------------------------------------------------------------------------

def _segment_days(window_start: date, window_end: date, today: date, horizon_days: int) -> dict:
    """Splits a driver's fixed window into three non-overlapping date
    ranges relative to today, each clipped to the window itself:
    "actual" (window_start..today, real data), "forecast" (today+1..
    today+horizon_days, a real short-range forecast), and "assumed"
    (whatever's left, a historical-scenario assumption). Returns
    (start, end) tuples or None for an empty segment.

    This one formula degrades correctly for every case without having to
    branch on the driver's status: a window that's fully in the past
    collapses to "actual" = the whole window; a window entirely beyond the
    forecast horizon collapses to "assumed" = the whole window; a window
    whose remainder is fully inside the forecast horizon leaves "assumed"
    empty. horizon_days=0 (used when the forecast fetch itself failed)
    collapses "forecast" to always-empty, folding everything not yet
    actual into "assumed" instead - the graceful-degradation path."""
    def clip(a, b):
        lo, hi = max(a, window_start), min(b, window_end)
        return (lo, hi) if lo <= hi else None

    return {
        "actual": clip(window_start, today),
        "forecast": clip(today + timedelta(days=1), today + timedelta(days=horizon_days)),
        "assumed": clip(today + timedelta(days=horizon_days + 1), window_end),
    }


def _segment_day_count(segment) -> int:
    return (segment[1] - segment[0]).days + 1 if segment else 0


def _scenario_raw_value(hist_values: list, direction: str, scenario: str):
    """The three what-if raw values a driver could plausibly take, drawn
    straight from the reference seasons already on file: "expected" is
    their mean, "favorable" is the best of them, "unfavorable" the
    worst - best/worst per the driver's own direction, so e.g. for a
    lower-is-worse driver "favorable" means the highest historical value."""
    if not hist_values:
        return None
    if scenario == "expected":
        return sum(hist_values) / len(hist_values)
    best = max(hist_values) if direction == "lower_is_worse" else min(hist_values)
    worst = min(hist_values) if direction == "lower_is_worse" else max(hist_values)
    return best if scenario == "favorable" else worst


def _project_driver(d: dict, state: dict, forecast_by_date: dict, horizon: int) -> dict:
    """One still-open driver's actual/forecast/assumed day-split and its
    three scenario-projected raw values (not yet converted to risk
    points). `horizon` is how many days past today the forecast segment may
    claim - the caller derives it from what the forecast provider actually
    returned (0 when unavailable), so this never counts a forecast day the
    data doesn't cover. If either the actual or forecast segment has days
    but no usable data (a genuine gap - a down sensor or an unreachable
    forecast provider), the whole driver falls back to the pure
    historical-scenario value for its entire window instead of blending a
    partial gap in as if it meant "no risk" - flagged via "data_gap" so the
    caller can tell."""
    year = state["current_year"]
    today = state["today"]
    window_start, window_end = _window_dates(year, d["window_md"])
    window_total_days = (window_end - window_start).days + 1
    segs = _segment_days(window_start, window_end, today, horizon)

    actual_days = _segment_day_count(segs["actual"])
    forecast_days = _segment_day_count(segs["forecast"])
    assumed_days = _segment_day_count(segs["assumed"])

    actual_value = _driver_value(_date_range_rows(state["by_date"], *segs["actual"]), d) if segs["actual"] else None
    forecast_value = _driver_value(_date_range_rows(forecast_by_date, *segs["forecast"]), d) if segs["forecast"] else None

    data_gap = (actual_days > 0 and actual_value is None) or (forecast_days > 0 and forecast_value is None)
    hist_values = state["hist_range"][d["key"]]

    if data_gap or not hist_values:
        scenarios = {s: _scenario_raw_value(hist_values, d["direction"], s)
                     for s in ("favorable", "expected", "unfavorable")}
        return {"data_gap": True, "actual_days": 0, "forecast_days": 0,
                "assumed_days": window_total_days, "scenarios": scenarios}

    scenarios = {}
    for s in ("favorable", "expected", "unfavorable"):
        scenario_value = _scenario_raw_value(hist_values, d["direction"], s)
        if d["agg"] in _INTENSIVE_AGGS:
            weighted = (actual_days * (actual_value or 0) + forecast_days * (forecast_value or 0)
                        + assumed_days * scenario_value)
            scenarios[s] = weighted / window_total_days
        else:
            rate = scenario_value / window_total_days
            scenarios[s] = (actual_value or 0) + (forecast_value or 0) + rate * assumed_days

    return {"data_gap": False, "actual_days": actual_days, "forecast_days": forecast_days,
            "assumed_days": assumed_days, "scenarios": scenarios}


def _ols_fit(xs: list, ys: list):
    """Hand-rolled least-squares line + Pearson r - no numpy/scipy in this
    project's dependencies, and n=6 makes a manual fit trivial. Returns
    None if there's too little variation to fit anything meaningful."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    slope = sxy / sxx
    intercept = my - slope * mx
    r = sxy / ((sxx ** 0.5) * (syy ** 0.5))
    return {"slope": round(slope, 2), "intercept": round(intercept, 1), "r": round(r, 3), "n_seasons": n}


def build_harvest_forecast(session: Session) -> dict:
    """Three kg predictions for the CURRENT season - Favorable/Expected/
    Unfavorable - rather than one falsely-precise number, since most of a
    season's outcome still depends on weather that hasn't happened yet.

    Each of the four DRIVERS' still-open window is projected forward (see
    _project_driver()): actual data where it exists, a real Open-Meteo
    forecast for the next 15 days, and a historical-scenario
    assumption (this driver's best/mean/worst of the reference seasons)
    for whatever's beyond that. Projected values are scored with the exact
    same _risk_points() used by the real Risk tab, so a scenario's 0-100
    score is directly comparable to any real season's score. That score is
    converted to a kg prediction via a simple linear regression fit on the
    historical (risk_score, total_kg) pairs (see _ols_fit(); this
    project's dependencies don't include numpy/scipy, hence hand-rolled).

    Two things worth knowing about the numbers this produces: the
    Unfavorable scenario combines each driver's own worst historical YEAR
    (four different real years, not one real season that was worst on all
    four at once), so its score can land outside anything a real season has
    scored - the four drivers are near-independent, which makes an
    all-four-at-their-worst season very unlikely. Rather than let the line
    extrapolate out there (which ran the unfavorable end past zero into
    negative kg), predictions are clamped to the fitted seasons' own kg
    span, so the extremes read as "as good/bad as the best/worst season on
    file" instead of a falsely precise number. And if the Open-Meteo forecast
    call itself fails, every driver just falls back to a pure
    historical-scenario projection for its whole open window (see
    forecast_unavailable in the return value) rather than the endpoint
    failing outright.
    """
    sync_recent_weather(session)  # keep "actual" as fresh as the Weather tab would
    state = _compute_driver_state(session)

    forecast_unavailable = False
    forecast_by_date = defaultdict(list)
    try:
        coords = farm_coords(session)
        if coords is None:
            # No farm location set - there is nothing to forecast for. This
            # would also be caught by the except below (unpacking None raises),
            # but relying on that would report "the provider is unreachable"
            # for what is really "nobody has said where this farm is".
            raise ValueError("farm location not set")
        lat, lon = coords
        # Short timeout (not the 30s default): this runs inside the same
        # request the frontend gives up on after 8s (Boord.NETWORK_TIMEOUT_MS),
        # and a slow forecast call already degrades gracefully below via
        # forecast_unavailable - it should do so quickly, not hang the whole
        # Risk tab offline while it waits.
        raw = fetch_forecast_hourly(lat, lon, days=FORECAST_API_DAYS, timeout=3)
        # lat/lon are stamped on these rows like any other, though nothing
        # here persists them - the forecast is held in memory for this one
        # request. Passing them keeps one parse function for every caller.
        for row in parse_hourly_rows(raw, lat, lon):
            forecast_by_date[row["timestamp"].date()].append(SimpleNamespace(**row))
    except Exception:
        forecast_unavailable = True

    # Trust what actually came back, not what we asked for: the horizon is
    # the last future day the provider really covered, capped at
    # FORECAST_HORIZON_DAYS. A short response (or a provider that changes
    # how forecast_days counts) then just shortens the forecast segment
    # rather than silently counting days that carry no data.
    horizon = 0
    if not forecast_unavailable:
        future = [d for d in forecast_by_date if d > state["today"]]
        horizon = min(FORECAST_HORIZON_DAYS, (max(future) - state["today"]).days) if future else 0
    forecast_horizon_end = state["today"] + timedelta(days=horizon) if horizon else None

    driver_data = []
    scenario_points = {"favorable": [], "expected": [], "unfavorable": []}
    for d in DRIVERS:
        status = state["status_by_year"][d["key"]][state["current_year"]]
        window_start, window_end = _window_dates(state["current_year"], d["window_md"])
        window_total_days = (window_end - window_start).days + 1
        hist_values = state["hist_range"][d["key"]]

        if status == "final":
            value = state["value_by_year"][d["key"]][state["current_year"]]
            if value is None or not hist_values:
                raw_vals = {s: _scenario_raw_value(hist_values, d["direction"], s)
                            for s in scenario_points} if hist_values else {s: None for s in scenario_points}
                data_gap, actual_days, forecast_days, assumed_days = True, 0, 0, window_total_days
            else:
                raw_vals = {s: value for s in scenario_points}
                data_gap, actual_days, forecast_days, assumed_days = False, window_total_days, 0, 0
        else:
            proj = _project_driver(d, state, forecast_by_date, horizon)
            raw_vals, data_gap = proj["scenarios"], proj["data_gap"]
            actual_days, forecast_days, assumed_days = proj["actual_days"], proj["forecast_days"], proj["assumed_days"]

        rp = {s: (_risk_points(raw_vals[s], hist_values, d["direction"]) if raw_vals[s] is not None else None)
              for s in scenario_points}
        for s in scenario_points:
            scenario_points[s].append(rp[s])

        driver_data.append({
            "key": d["key"], "label": d["label"], "window": d["window_label"], "unit": d["unit"],
            "status": status, "actual_days": actual_days, "forecast_days": forecast_days,
            "assumed_days": assumed_days, "data_gap": data_gap,
            "scenarios": {s: {"value": round(raw_vals[s], 1) if raw_vals[s] is not None else None,
                               "risk_points": rp[s]} for s in scenario_points},
        })

    # Historical (risk_score, total_kg) pairs for the regression - computed
    # directly from `state` rather than by calling build_risk_summary()
    # (which would reload and re-group the entire WeatherHistory table a
    # second time via its own _compute_driver_state() call). Every
    # historical year is always "final" on every driver by definition
    # (they're all in the past), so this reproduces exactly what
    # build_risk_summary() would report as that season's risk_score.
    hist_pairs = []
    regression_years = []
    for year in state["historical_years"]:
        if year < REGRESSION_START_YEAR:
            continue  # immature orchard - see REGRESSION_START_YEAR
        regression_years.append(year)
        comp_scores = [
            _risk_points(state["value_by_year"][d["key"]][year], state["hist_range"][d["key"]], d["direction"])
            for d in DRIVERS
        ]
        if all(p is not None for p in comp_scores):
            hist_pairs.append((round(sum(comp_scores), 1), state["kg_by_year"].get(year, 0.0)))
    regression = _ols_fit([p[0] for p in hist_pairs], [p[1] for p in hist_pairs])
    reference_avg_kg = sum(p[1] for p in hist_pairs) / len(hist_pairs) if hist_pairs else None
    # The fitted seasons' own kg span. A scenario score outside the fitted
    # score range would otherwise run the line off into kg totals no season
    # has ever come close to - and, at the unfavorable end, straight past
    # zero into negative kg. Clamping to what's actually been recorded keeps
    # the extremes honest ("as bad as the worst season on file") instead of
    # falsely precise, the same way _risk_points() clamps its own inputs.
    kg_lo = min(p[1] for p in hist_pairs) if hist_pairs else None
    kg_hi = max(p[1] for p in hist_pairs) if hist_pairs else None

    scenarios_out = {}
    for s, points in scenario_points.items():
        if any(p is None for p in points):
            scenarios_out[s] = {"risk_score": None, "band": None, "predicted_kg": None, "vs_avg_pct": None}
            continue
        score = round(sum(points), 1)
        predicted_kg = None
        vs_avg_pct = None
        if regression:
            fitted = regression["intercept"] + regression["slope"] * score
            predicted_kg = round(min(kg_hi, max(kg_lo, fitted)), 1)
            if reference_avg_kg:
                vs_avg_pct = round((predicted_kg - reference_avg_kg) / reference_avg_kg * 100, 1)
        scenarios_out[s] = {"risk_score": score, "band": _band(score),
                             "predicted_kg": predicted_kg, "vs_avg_pct": vs_avg_pct}

    return {
        "current_year": state["current_year"],
        "forecast_unavailable": forecast_unavailable,
        "forecast_horizon_end": forecast_horizon_end.isoformat() if forecast_horizon_end else None,
        "reference_avg_kg": round(reference_avg_kg, 1) if reference_avg_kg is not None else None,
        "reference_label": _reference_label(state["historical_years"]),
        # Distinct from reference_label above, and deliberately so: the
        # scenarios' historical range spans every reference season, but the
        # kg line (and reference_avg_kg with it) is fitted only from
        # REGRESSION_START_YEAR. Labelling the average with the wider range
        # would credit it to seasons it was never computed over.
        "regression_label": _reference_label(regression_years),
        "reference_season_count": len(hist_pairs),
        "regression": regression,
        "scenarios": scenarios_out,
        "drivers": driver_data,
    }


@router.get("/forecast")
def risk_forecast(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Harvest Forecast data - see build_harvest_forecast(). No caller in
    Boord's own UI since the forecast card left with the Owner View; kept
    as that app's API, same as every other endpoint here."""
    return build_harvest_forecast(session)
