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
