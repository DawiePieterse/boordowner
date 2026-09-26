"""Pure-function tests for the Risk indicator and Harvest Forecast maths.

Ported from Boord's scripts/selftest.py (recoverable in full from
../Boord git 2226750 - the parts not ported there cover Boord's Alembic
migrations, backup snapshots and the XLSX report, none of which this app
has). No database: exact expected values.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from models_owner import WeatherHistory
from routers.risk import (DRIVERS, REFERENCE_START_YEAR, REGRESSION_START_YEAR,
                          _band, _driver_value, _ols_fit, _project_driver,
                          _reference_label, _risk_points, _segment_days,
                          _window_status, build_risk_summary)


def test_risk_points_scaling():
    hist = [10.0, 20.0]
    assert _risk_points(10.0, hist, "lower_is_worse") == 25.0
    assert _risk_points(20.0, hist, "lower_is_worse") == 0.0
    assert _risk_points(15.0, hist, "lower_is_worse") == 12.5
    assert _risk_points(20.0, hist, "higher_is_worse") == 25.0
    assert _risk_points(10.0, hist, "higher_is_worse") == 0.0


def test_risk_points_clamps_beyond_history():
    hist = [10.0, 20.0]
    assert _risk_points(5.0, hist, "lower_is_worse") == 25.0
    assert _risk_points(99.0, hist, "lower_is_worse") == 0.0
    assert _risk_points(99.0, hist, "higher_is_worse") == 25.0
    assert _risk_points(-99.0, hist, "higher_is_worse") == 0.0


def test_risk_points_degenerate():
    assert _risk_points(None, [1.0, 2.0], "lower_is_worse") is None
    assert _risk_points(1.0, [], "lower_is_worse") is None
    assert _risk_points(5.0, [5.0, 5.0], "lower_is_worse") == 12.5


def test_band_boundaries():
    assert _band(0) == "Low"
    assert _band(24.9) == "Low"
    assert _band(25) == "Moderate"
    assert _band(49.9) == "Moderate"
    assert _band(50) == "Elevated"
    assert _band(74.9) == "Elevated"
    assert _band(75) == "High"
    assert _band(100) == "High"


def test_driver_value_aggregations():
    def row(ts, **kw):
        return SimpleNamespace(timestamp=ts, **kw)
    day = datetime(2024, 10, 1)
    rows = [row(day + timedelta(hours=h), temp_c=float(h), precipitation_mm=1.0,
                sunshine_duration_s=3600.0) for h in range(24)]
    rows += [row(day + timedelta(days=1, hours=h), temp_c=float(h) + 10,
                 precipitation_mm=0.0, sunshine_duration_s=1800.0) for h in range(24)]
    assert _driver_value(rows, {"field": "temp_c", "agg": "count_lt", "threshold": 5}) == 5.0
    assert _driver_value(rows, {"field": "temp_c", "agg": "count_gt", "threshold": 30}) == 3.0
    assert _driver_value(rows, {"field": "temp_c", "agg": "mean"}) == 16.5
    assert _driver_value(rows, {"field": "temp_c", "agg": "daily_max_mean"}) == 28.0
    assert _driver_value(rows, {"field": "precipitation_mm", "agg": "sum"}) == 24.0
    assert _driver_value(rows, {"field": "precipitation_mm", "agg": "count_days_gt", "threshold": 0.5}) == 1.0
    assert _driver_value(rows, {"field": "sunshine_duration_s", "agg": "sum", "scale": 1 / 3600}) == 36.0


# --------------------------------------------------------------------------- #
# today/station_today: folding the farm's own on-site iWeathar station into
# TODAY's contribution to the two drivers it can actually improve on
# (fruit_warmth, sizing_rain - see routers/risk.py's DRIVERS comment).
# --------------------------------------------------------------------------- #
def test_driver_value_today_override_rain_replaces_not_adds():
    def row(ts, **kw):
        return SimpleNamespace(timestamp=ts, **kw)
    today = date(2024, 10, 5)
    rows = [row(datetime(2024, 10, 1, 12), precipitation_mm=5.0),
            row(datetime(2024, 10, 5, 6), precipitation_mm=2.0),
            row(datetime(2024, 10, 5, 18), precipitation_mm=3.0)]
    driver = {"field": "precipitation_mm", "agg": "sum"}
    # Without the override, today's two modelled hours (2.0 + 3.0) are summed
    # like any other day.
    assert _driver_value(rows, driver) == 10.0
    # With it, today's modelled hours are dropped entirely and the gauge's
    # real running total substituted in their place - not added on top of
    # its own 5.0mm, since both describe the same rain.
    got = _driver_value(rows, driver, today=today, station_today={"rain_today_mm": 8.0})
    assert got == 13.0  # 5.0 (Oct 1, untouched) + 8.0 (today, from the gauge)


def test_driver_value_today_override_temp_takes_the_higher_peak():
    def row(ts, **kw):
        return SimpleNamespace(timestamp=ts, **kw)
    today = date(2024, 10, 5)
    rows = [row(datetime(2024, 10, 1, 14), temp_c=20.0),
            row(datetime(2024, 10, 5, 9), temp_c=18.0)]
    driver = {"field": "temp_c", "agg": "daily_max_mean"}
    # The station's own running max for today (22.0) beats anything synced
    # from Open-Meteo so far (18.0), so it wins.
    got = _driver_value(rows, driver, today=today, station_today={"temp_max_c": 22.0})
    assert got == (20.0 + 22.0) / 2
    # A lower station reading never pulls the peak down - both numbers are
    # running maxima over the SAME still-open day, so the true max-so-far is
    # whichever is higher, not a replacement.
    got_lower = _driver_value(rows, driver, today=today, station_today={"temp_max_c": 5.0})
    assert got_lower == (20.0 + 18.0) / 2


def test_driver_value_today_override_needs_the_matching_key():
    def row(ts, **kw):
        return SimpleNamespace(timestamp=ts, **kw)
    today = date(2024, 10, 5)
    rows = [row(datetime(2024, 10, 5, 12), precipitation_mm=1.0)]
    # station_today present but missing the one key this driver reads (e.g.
    # the station answered but its Rainfall Today field didn't parse) - the
    # override is a no-op, not a crash or a silent zero.
    assert _driver_value(rows, {"field": "precipitation_mm", "agg": "sum"},
                         today=today, station_today={}) == 1.0
    # No station_today at all (unconfigured or unreachable) behaves exactly
    # as it always has.
    assert _driver_value(rows, {"field": "precipitation_mm", "agg": "sum"}) == 1.0


def test_driver_value_missing_data():
    def row(ts, v):
        return SimpleNamespace(timestamp=ts, temp_c=v)
    day = datetime(2024, 10, 1)
    assert _driver_value([], {"field": "temp_c", "agg": "mean"}) is None
    allnull = [row(day, None), row(day, None)]
    assert _driver_value(allnull, {"field": "temp_c", "agg": "mean"}) is None
    assert _driver_value(allnull, {"field": "temp_c", "agg": "daily_max_mean"}) is None
    mixed = [row(day, None), row(day, 10.0)]
    assert _driver_value(mixed, {"field": "temp_c", "agg": "mean"}) == 10.0


def test_every_driver_agg_is_implemented():
    def row(ts, **kw):
        return SimpleNamespace(timestamp=ts, **kw)
    day = datetime(2024, 10, 1)
    for d in DRIVERS:
        rows = [row(day + timedelta(hours=h), **{d["field"]: 1.0}) for h in range(3)]
        _driver_value(rows, d)


def test_window_status_transitions():
    wmd = ((9, 16), (10, 31))
    assert _window_status(2025, wmd, date(2025, 9, 15)) == "pending"
    assert _window_status(2025, wmd, date(2025, 9, 16)) == "in_progress"
    assert _window_status(2025, wmd, date(2025, 10, 31)) == "in_progress"
    assert _window_status(2025, wmd, date(2025, 11, 1)) == "final"


def test_segment_days_partition():
    ws, we = date(2025, 10, 1), date(2025, 10, 30)
    total = (we - ws).days + 1
    for offset in range(-5, 36):
        today = ws + timedelta(days=offset)
        for horizon in (0, 3, 15, 60):
            segs = _segment_days(ws, we, today, horizon)
            seen = set()
            days = 0
            for seg in segs.values():
                if seg is None:
                    continue
                assert seg[0] <= seg[1]
                assert ws <= seg[0] and seg[1] <= we
                d = seg[0]
                while d <= seg[1]:
                    assert d not in seen
                    seen.add(d)
                    d += timedelta(days=1)
                    days += 1
            if today >= we:
                assert days == total
            if today < ws and horizon == 0:
                assert segs["assumed"] == (ws, we)


def test_segment_days_zero_horizon_has_no_forecast():
    segs = _segment_days(date(2025, 10, 1), date(2025, 10, 30), date(2025, 10, 10), 0)
    assert segs["forecast"] is None
    assert segs["actual"] == (date(2025, 10, 1), date(2025, 10, 10))
    assert segs["assumed"] == (date(2025, 10, 11), date(2025, 10, 30))


def test_ols_fit():
    fit = _ols_fit([0.0, 1.0, 2.0, 3.0], [1.0, 3.0, 5.0, 7.0])
    assert abs(fit["slope"] - 2.0) < 1e-6
    assert abs(fit["intercept"] - 1.0) < 1e-6
    assert abs(fit["r"] - 1.0) < 1e-6
    assert fit["n_seasons"] == 4
    assert _ols_fit([1.0], [1.0]) is None
    assert _ols_fit([1.0, 1.0], [1.0, 2.0]) is None


def test_reference_label():
    assert _reference_label([2012, 2013, 2025]) == "2012-2025"
    assert _reference_label([2019]) == "2019"
    assert _reference_label([]) == ""


def test_project_driver_intensive_vs_extensive():
    state = {
        "current_year": 2025, "today": date(2025, 10, 5),
        "by_date": defaultdict(list), "hist_range": {"k": [100.0, 200.0]},
    }
    for d in range(1, 6):
        for h in range(24):
            state["by_date"][date(2025, 10, d)].append(
                SimpleNamespace(timestamp=datetime(2025, 10, d, h), v=10.0 / 24))
    ext = {"key": "k", "window_md": ((10, 1), (10, 10)), "field": "v", "agg": "sum",
           "direction": "lower_is_worse"}
    got = _project_driver(ext, state, defaultdict(list), 0)
    assert got["actual_days"] == 5 and got["assumed_days"] == 5
    assert abs(got["scenarios"]["expected"] - (50.0 + 15.0 * 5)) < 1e-6
    inten = dict(ext, agg="mean")
    got = _project_driver(inten, state, defaultdict(list), 0)
    actual_mean = 10.0 / 24
    assert abs(got["scenarios"]["expected"] - (5 * actual_mean + 5 * 150.0) / 10) < 1e-6


def test_project_driver_actual_segment_uses_station_today():
    """The actual segment always ends at `today` (_segment_days clips it
    there), so a configured on-site station's reading for today folds into
    it via the same today/station_today path _driver_value uses directly -
    see the today-override tests above."""
    state = {
        "current_year": 2025, "today": date(2025, 10, 5),
        "by_date": defaultdict(list),
        "hist_range": {"sizing_rain": [50.0, 150.0]},
        "station_today": {"rain_today_mm": 40.0},
    }
    for d in range(1, 6):
        state["by_date"][date(2025, 10, d)].append(
            SimpleNamespace(timestamp=datetime(2025, 10, d, 12), precipitation_mm=2.0))
    driver = {"key": "sizing_rain", "window_md": ((10, 1), (10, 10)),
             "field": "precipitation_mm", "agg": "sum", "direction": "lower_is_worse"}
    got = _project_driver(driver, state, defaultdict(list), 0)
    assert got["actual_days"] == 5 and got["assumed_days"] == 5
    # actual: Oct 1-4 keep their modelled 2.0mm each (8.0mm); Oct 5 (today)
    # is replaced outright by the station's 40.0mm gauge reading, not added
    # to its own modelled 2.0mm - 48.0mm total.
    # assumed: the 5 remaining days at the "expected" scenario's rate
    # (mean([50, 150]) / 10-day window = 10.0mm/day) = 50.0mm.
    assert abs(got["scenarios"]["expected"] - (48.0 + 50.0)) < 1e-6


def test_project_driver_data_gap_falls_back():
    state = {"current_year": 2025, "today": date(2025, 10, 5),
             "by_date": defaultdict(list), "hist_range": {"k": [100.0, 200.0]}}
    d = {"key": "k", "window_md": ((10, 1), (10, 10)), "field": "v", "agg": "sum",
         "direction": "lower_is_worse"}
    got = _project_driver(d, state, defaultdict(list), 0)
    assert got["data_gap"] is True
    assert got["assumed_days"] == 10 and got["actual_days"] == 0
    assert got["scenarios"]["expected"] == 150.0


def test_driver_definitions_wellformed():
    seen = set()
    for d in DRIVERS:
        for key in ("key", "label", "window_md", "window_label", "field", "agg",
                    "unit", "direction", "why"):
            assert key in d, f"{d.get('key')} missing {key}"
        assert d["key"] not in seen
        seen.add(d["key"])
        assert d["direction"] in ("lower_is_worse", "higher_is_worse")
        (sm, sd), (em, ed) = d["window_md"]
        assert date(2024, sm, sd) <= date(2024, em, ed)
        assert sm <= em, f"{d['key']} window spans a calendar year"
        assert hasattr(WeatherHistory, d["field"])


def test_regression_start_not_before_reference_start():
    assert REGRESSION_START_YEAR >= REFERENCE_START_YEAR


def test_build_risk_summary_empty_dbs(client):
    """End-to-end against the fake Boord DB + an empty owner.db: no weather,
    no history -> still a well-formed response with every driver listed."""
    from db import boord_engine, owner_engine
    from sqlmodel import Session
    with Session(boord_engine) as boord, Session(owner_engine) as owner:
        out = build_risk_summary(boord, owner)
    assert out["driver_count"] == 4
    assert len(out["drivers"]) == 4
    assert out["current_year"] == 2026


def test_forecast_weight_fades_with_lead_time():
    from routers.risk import FORECAST_TRUST_DAYS, _forecast_weight
    assert _forecast_weight(1) == 1.0
    assert 0 < _forecast_weight(FORECAST_TRUST_DAYS) < 1
    assert _forecast_weight(FORECAST_TRUST_DAYS + 1) == 0.0
    assert _forecast_weight(15) == 0.0


def test_project_driver_forecast_fades_toward_assumed():
    """A far-out forecast day is nearly all historical assumption; a day-1
    forecast is taken as-is. So the same 30mm shower moves the projection
    a lot when it's tomorrow and hardly at all when it's 12 days out."""
    from routers.risk import _forecast_weight
    state = {"current_year": 2025, "today": date(2025, 10, 5),
             "by_date": defaultdict(list), "hist_range": {"k": [100.0, 200.0]}}
    for d in range(1, 6):
        state["by_date"][date(2025, 10, d)].append(
            SimpleNamespace(timestamp=datetime(2025, 10, d, 12), v=0.0))
    ext = {"key": "k", "window_md": ((10, 1), (10, 20)), "field": "v", "agg": "sum",
           "direction": "lower_is_worse"}
    rate = 150.0 / 20  # expected per-day share of the 20-day window

    def project(shower_day):
        fc = defaultdict(list)
        for lead in range(1, 16):
            day = date(2025, 10, 5) + timedelta(days=lead)
            fc[day].append(SimpleNamespace(timestamp=datetime(day.year, day.month, day.day, 12),
                                           v=30.0 if lead == shower_day else 0.0))
        return _project_driver(ext, state, fc, 15)

    base = project(shower_day=None)
    assert base["forecast_days"] == 15 and base["assumed_days"] == 0
    # No rain forecast at all: day 1 contributes 0, days 8+ the full assumed rate.
    expected_base = sum(_forecast_weight(l) * 0.0 + (1 - _forecast_weight(l)) * rate for l in range(1, 16))
    assert abs(base["scenarios"]["expected"] - expected_base) < 1e-6

    near = project(shower_day=1)["scenarios"]["expected"] - base["scenarios"]["expected"]
    far = project(shower_day=12)["scenarios"]["expected"] - base["scenarios"]["expected"]
    assert abs(near - 30.0) < 1e-6
    assert far == 0.0
