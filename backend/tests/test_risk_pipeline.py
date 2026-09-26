"""The Risk score and Harvest Forecast end to end, with real reference data
on file - every other risk test either works on empty databases (the
degrade-gracefully paths) or on one helper at a time.

Four reference seasons (2022-2025) get a season total (HistoricalAnnualYield)
and weather through every driver window, varied so the drivers' reference
ranges have spread and the kg regression has something to fit. "Today" is
pinned so the current season's in-progress/final/pending split is known.
"""
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session

import routers.risk as risk_module
from db import owner_engine
from models_owner import HistoricalAnnualYield, WeatherHistory

TODAY = date(2026, 9, 26)
REFERENCE = [2022, 2023, 2024, 2025]
# Season total rises with a "better" weather offset below, so the fit has a
# real slope to find.
KG = {2022: 20000.0, 2023: 26000.0, 2024: 32000.0, 2025: 38000.0}


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return TODAY


def _seed():
    with Session(owner_engine) as s:
        for i, year in enumerate(REFERENCE):
            s.add(HistoricalAnnualYield(block_id="7", season_year=year, kg=KG[year]))
        for i, year in enumerate(REFERENCE + [2026]):
            # Later seasons: wetter (sizing_rain up), cooler afternoons
            # (fruit_warmth down), moister air (spring_dryness up), sunnier
            # flowering - all "better" per DRIVERS' directions.
            d = date(year, 8, 1)
            end = min(date(year, 11, 30), TODAY) if year == 2026 else date(year, 11, 30)
            while d <= end:
                for hour in (0, 6, 12, 18):
                    s.add(WeatherHistory(
                        timestamp=datetime(d.year, d.month, d.day, hour),
                        temp_c=(30.0 - i) if hour == 12 else 18.0,
                        dew_point_c=8.0 + i, precipitation_mm=0.2 * i,
                        sunshine_duration_s=1800.0 * (1 + i) if hour == 12 else 0.0,
                        lat=-34.0, lon=18.5))
                d += timedelta(days=1)
        s.commit()


@pytest.fixture()
def pipeline(client, monkeypatch):
    monkeypatch.setattr(risk_module, "date", _FrozenDate)
    monkeypatch.setattr(risk_module, "sync_recent_weather", lambda owner, boord: {"synced": 0})
    monkeypatch.setattr(risk_module, "fetch_forecast_hourly",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no forecast")))
    _seed()
    return client


def test_reference_seasons_score_and_the_current_one_waits(pipeline):
    body = pipeline.get("/api/risk/summary").json()
    assert body["historical_years"] == REFERENCE
    by_year = {s["year"]: s for s in body["seasons"]}
    for year in REFERENCE:
        s = by_year[year]
        assert s["known_count"] == 4
        assert s["risk_score"] is not None and s["band"] is not None
        assert s["total_kg"] == KG[year]
    # Better weather season on season -> lower risk score.
    scores = [by_year[y]["risk_score"] for y in REFERENCE]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 100.0 and scores[-1] == 0.0   # the worst and best on every driver

    current = by_year[2026]
    assert current["is_current"]
    status = {c["key"]: c["status"] for c in current["components"]}
    assert status == {"flowering_sun": "final", "spring_dryness": "in_progress",
                      "fruit_warmth": "in_progress", "sizing_rain": "pending"}
    assert current["known_count"] == 1
    assert current["risk_score"] is None          # never a partial score sold as final
    assert current["score_so_far"] is not None


def test_forecast_projects_the_open_windows_and_stays_within_the_record(pipeline):
    body = pipeline.get("/api/risk/summary").json()
    f = body["forecast"]
    assert f["forecast_unavailable"] is True      # the live fetch was made to fail
    assert f["regression"]["n_seasons"] == 4
    assert f["regression"]["slope"] < 0           # more risk points, fewer kg
    assert f["reference_avg_kg"] == sum(KG.values()) / 4

    drivers = {d["key"]: d for d in f["drivers"]}
    assert drivers["flowering_sun"]["status"] == "final"
    assert drivers["flowering_sun"]["actual_days"] == 46 and drivers["flowering_sun"]["assumed_days"] == 0
    assert drivers["sizing_rain"]["status"] == "pending"
    assert drivers["sizing_rain"]["assumed_days"] == 61 and drivers["sizing_rain"]["actual_days"] == 0
    # 16 Sep - 26 Sep actual, no forecast (unavailable), the rest assumed.
    warmth = drivers["fruit_warmth"]
    assert (warmth["actual_days"], warmth["forecast_days"]) == (11, 0)
    assert warmth["actual_days"] + warmth["assumed_days"] == 61

    sc = f["scenarios"]
    assert sc["favorable"]["risk_score"] <= sc["expected"]["risk_score"] <= sc["unfavorable"]["risk_score"]
    lo, hi = min(KG.values()), max(KG.values())
    for s in sc.values():
        assert lo <= s["predicted_kg"] <= hi
        assert s["predicted_kg"] % risk_module.KG_ROUNDING == 0 or s["predicted_kg"] in (lo, hi)
    assert sc["favorable"]["predicted_kg"] >= sc["expected"]["predicted_kg"] >= sc["unfavorable"]["predicted_kg"]
