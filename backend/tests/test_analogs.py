"""Similar past seasons (routers/analogs.py): which seasons' weather came
closest over the same calendar days, and what they produced."""
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session

import routers.analogs as analogs_module
from db import owner_engine
from models_owner import HistoricalAnnualYield, HistoricalHarvest, WeatherHistory

TODAY = date(2026, 10, 3)   # flowering closed; dryness/warmth 17 days in; rain 2 days in (too few)


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return TODAY


# Per season: (sunshine seconds per day, dew point, afternoon temp, rain mm/day).
# 2026 is the season being estimated. 2019 and 1998 match it exactly; 2024
# matches it on every day observed so far and only differs AFTER 2 Oct.
TARGET = (30_000, 10.0, 28.0, 2.0)
WEATHER = {
    1998: TARGET, 2019: TARGET, 2024: TARGET, 2026: TARGET,
    2013: (26_000, 12.0, 29.0, 2.0),
    2016: (20_000, 14.0, 31.0, 1.0), 2017: (24_000, 11.0, 29.0, 2.0),
    2018: (25_000, 12.0, 30.0, 3.0), 2020: (35_000, 8.0, 26.0, 4.0),
    2021: (22_000, 13.0, 29.0, 1.0), 2022: (40_000, 6.0, 25.0, 5.0),
    2023: (27_000, 11.0, 27.0, 2.0), 2025: (33_000, 9.0, 30.0, 3.0),
}
KG = {2013: 30_000, 2016: 200_000, 2017: 160_000, 2018: 60_000, 2019: 90_000,
      2020: 100_000, 2021: 40_000, 2022: 120_000, 2023: 80_000, 2024: 20_000, 2025: 60_000}


def _seed(weather=True):
    with Session(owner_engine) as s:
        for y, kg in KG.items():
            if y < 2020:
                s.add(HistoricalAnnualYield(block_id="7", season_year=y, kg=kg))
            else:
                # Block 7: a trickle on 1 Sep then the bulk on 1 Nov.
                s.add(HistoricalHarvest(block_id="7", harvest_date=date(y, 9, 1), season_year=y, kg=kg * 0.01))
                s.add(HistoricalHarvest(block_id="7", harvest_date=date(y, 11, 1), season_year=y, kg=kg * 0.99))
        s.add(HistoricalHarvest(block_id="8a", harvest_date=date(2024, 11, 1), season_year=2024, kg=5_000))
        s.add(HistoricalAnnualYield(block_id=None, season_year=1998, kg=1_148_028))
        s.add(HistoricalAnnualYield(block_id=None, season_year=1999, kg=382_130))
        s.commit()
    if not weather:
        return
    rows = []
    for y, (sun, dew, temp, rain) in WEATHER.items():
        d = date(y, 8, 1)
        end = date(2026, 10, 2) if y == 2026 else date(y, 11, 30)
        while d <= end:
            dew_d = 20.0 if (y == 2024 and d > date(y, 10, 2)) else dew
            # 2017 is missing half its flowering window.
            if not (y == 2017 and d < date(y, 8, 24)):
                rows.append({"timestamp": datetime.combine(d, datetime.min.time()) + timedelta(hours=23),
                             "sunshine_duration_s": sun, "dew_point_c": dew_d, "temp_c": temp,
                             "precipitation_mm": rain, "condition": ""})
            d += timedelta(days=1)
    with owner_engine.begin() as conn:
        conn.execute(WeatherHistory.__table__.insert(), rows)


@pytest.fixture()
def frozen(client, monkeypatch):
    monkeypatch.setattr(analogs_module, "date", _FrozenDate)
    return client


def test_compares_the_same_days_and_ranks_ties_newest_first(frozen):
    _seed()
    body = frozen.get("/api/estimate/analogs").json()
    assert body["state"] == "ok" and body["season_year"] == 2026
    assert body["cutoff"] == "2026-10-02" and body["weather_through"] == "2026-10-02"
    f = {x["key"]: x for x in body["factors"]}
    assert f["flowering_sun"]["status"] == "final" and f["flowering_sun"]["weight"] == 1.0
    assert f["spring_dryness"]["status"] == "partial" and f["spring_dryness"]["observed_days"] == 17
    assert f["spring_dryness"]["weight"] == round(17 / 46, 3)
    assert f["spring_dryness"]["compared_until"] == "02 Oct"
    assert f["fruit_warmth"]["weight"] == round(17 / 61, 3)
    # Open two days: too few to compare yet.
    assert f["sizing_rain"]["status"] == "too_short" and f["sizing_rain"]["weight"] == 0
    # 2024 differs only after 2 Oct, so so far it is as close as the exact
    # matches - and the newest of three ties comes first.
    years = [a["year"] for a in body["analogs"] if a["in_top"]]
    assert years[:3] == [2024, 2019, 1998]
    top = [a for a in body["analogs"] if a["in_top"]]
    assert all(a["distance"] == 0 and a["closeness"] == "close" for a in top[:3])
    assert abs(sum(a["weight"] for a in top) - 1) < 0.01
    assert len(top) == 5   # 11 comparable seasons -> min(5, max(2, 11 // 2))


def test_candidate_pool_rules(frozen):
    _seed()
    body = frozen.get("/api/estimate/analogs").json()
    excluded = {e["year"]: e["reason"] for e in body["excluded"]}
    assert excluded[2013] == "young_orchard"          # replanted orchard not yet bearing
    assert excluded[2017] == "weather_incomplete"     # half its flowering window missing
    assert excluded[1999] == "no_weather"
    seen = {a["year"] for a in body["analogs"]}
    assert 2026 not in seen and 2013 not in seen
    old = next(a for a in body["analogs"] if a["year"] == 1998)
    # Old-orchard seasons count only against their own era's average...
    assert old["record"] == "whole_farm"
    assert old["vs_avg_pct"] == round((1_148_028 / ((1_148_028 + 382_130) / 2) - 1) * 100, 1)
    # ...and never in the per-block figures.
    assert 1998 not in body["block_basis_years"]
    new = next(a for a in body["analogs"] if a["year"] == 2019)
    # 2016-2025 (2017 too: its weather is incomplete, its crop is not),
    # with 8a's 5 t in 2024.
    per_block_mean = (sum(v for y, v in KG.items() if y >= 2016) + 5_000) / len([y for y in KG if y >= 2016])
    assert new["vs_avg_pct"] == round((90_000 / per_block_mean - 1) * 100, 1)
    assert body["spread"]["n"] == 5


def test_block_basis(frozen):
    _seed()
    body = frozen.get("/api/estimate/analogs").json()
    assert {2019, 2024} < set(body["block_basis_years"]) and len(body["block_basis_years"]) == 3
    dist = {a["year"]: a["distance"] for a in body["analogs"]}
    third = [y for y in body["block_basis_years"] if y not in (2019, 2024)][0]
    # Block 7 on 2000 trees: weights 1/max(d, 0.1), over seasons it bore in.
    w = {2019: 10.0, 2024: 10.0, third: 1 / max(dist[third], 0.1)}
    expected = sum(w[y] * KG[y] / 2000 for y in w) / sum(w.values())
    assert body["blocks"]["7"] == round(expected, 1)
    # 8a (500 trees) only bore in 2024 among these seasons.
    assert body["blocks"]["8a"] == round(5_000 / 500, 1)
    # Another grower's block is not this farm's.
    assert "90" not in body["blocks"]


def test_descriptors_are_relative_to_each_seasons_own_total(frozen):
    _seed()
    body = frozen.get("/api/estimate/analogs").json()
    a = next(a for a in body["analogs"] if a["year"] == 2024)
    # 1% on 1 Sep is under the 2% start threshold; the season really got
    # going on 1 Nov.
    assert a["timing"] == {"first_date": "2024-09-01", "start_date": "2024-11-01",
                           "last_date": "2024-11-01", "span_days": 1}
    assert next(a for a in body["analogs"] if a["year"] == 2019)["timing"] is None   # annual-only
    assert body["target_timing"]["first_date"].startswith("2026-")


def test_future_season_and_no_weather(frozen):
    _seed()
    body = frozen.get("/api/estimate/analogs?season=2027").json()
    assert body["state"] == "no_weather_yet" and body["first_window_opens"] == "2027-08-01"
    assert body["analogs"] == [] and body["spread"] is None


def test_empty_weather_record(frozen):
    _seed(weather=False)
    body = frozen.get("/api/estimate/analogs").json()
    assert body["state"] == "no_weather" and body["no_location"] is True and body["analogs"] == []


def test_too_few_seasons(frozen):
    rows = []
    for y in (2025, 2026):
        d = date(y, 8, 1)
        while d <= date(y, 10, 2):
            rows.append({"timestamp": datetime.combine(d, datetime.min.time()) + timedelta(hours=23),
                         "sunshine_duration_s": 30_000.0, "dew_point_c": 10.0, "temp_c": 28.0,
                         "precipitation_mm": 1.0, "condition": ""})
            d += timedelta(days=1)
    with owner_engine.begin() as conn:
        conn.execute(WeatherHistory.__table__.insert(), rows)
    with Session(owner_engine) as s:
        s.add(HistoricalAnnualYield(block_id="7", season_year=2025, kg=10.0))
        s.commit()
    body = frozen.get("/api/estimate/analogs").json()
    assert body["state"] == "too_few_seasons"
    # This season's own weather is still shown, not marked "no data".
    f = {x["key"]: x for x in body["factors"]}
    assert f["flowering_sun"]["status"] == "final" and f["flowering_sun"]["target_value"] is not None


def test_no_network_and_boord_released(frozen, monkeypatch):
    import routers.risk as risk_module
    import weather as weather_module
    from db import boord_engine
    from tests.test_boord_isolation import _OpenConnectionCounter
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network"))
    for mod, name in [(weather_module, "fetch_historical_hourly"), (weather_module, "fetch_iweathar_current"),
                      (weather_module, "fetch_forecast_hourly"), (risk_module, "fetch_forecast_hourly")]:
        monkeypatch.setattr(mod, name, boom)
    _seed()
    with _OpenConnectionCounter(boord_engine) as counter:
        assert frozen.get("/api/estimate/analogs").status_code == 200
        assert counter.live == 0


def test_cached_weather_still_sees_a_new_harvest_import(frozen):
    """The weather figures are cached until WeatherHistory changes; the
    crops are not, so re-importing a season's kg shows at once."""
    _seed()
    before = next(a for a in frozen.get("/api/estimate/analogs").json()["analogs"] if a["year"] == 2019)
    assert before["kg"] == 90_000
    csv = "season_year,block_id,kg\n" + "".join(
        f"{y},7,{kg if y != 2019 else 95_000}\n" for y, kg in KG.items() if y < 2020) + "1998,,1148028\n"
    r = frozen.post("/api/historical-annual-yield/import", files={"file": ("a.csv", csv, "text/csv")})
    assert r.status_code == 200
    after = next(a for a in frozen.get("/api/estimate/analogs").json()["analogs"] if a["year"] == 2019)
    assert after["kg"] == 95_000


def test_stale_weather_is_behind_not_pending(frozen):
    """The Estimate tab never fetches weather. A record that stops before
    this season's windows opened is "behind", not "nothing to compare yet"."""
    _seed()
    with owner_engine.begin() as conn:
        conn.execute(WeatherHistory.__table__.delete().where(WeatherHistory.timestamp >= datetime(2026, 1, 1)))
    body = frozen.get("/api/estimate/analogs").json()
    assert body["state"] == "weather_behind" and body["weather_behind"] is True
    assert {f["status"] for f in body["factors"]} == {"behind"}
    # A window that genuinely hasn't opened is still pending.
    early = _FrozenDate(2026, 7, 20)
    import routers.analogs as m
    body = m.build_analogs(*_sessions(), None, early)
    assert body["state"] == "no_weather_yet" and {f["status"] for f in body["factors"]} == {"pending"}


def _sessions():
    from db import boord_engine
    return Session(boord_engine), Session(owner_engine)


def test_a_running_total_with_missing_days_is_scaled_to_the_window(frozen):
    """A season identical to this one but for 4 missing flowering days (91%
    coverage, so it counts) must not read as duller: sunshine is a sum."""
    _seed()
    rows = []
    d = date(2005, 8, 1)
    while d <= date(2005, 11, 30):
        if not date(2005, 8, 10) <= d <= date(2005, 8, 13):
            rows.append({"timestamp": datetime.combine(d, datetime.min.time()) + timedelta(hours=23),
                         "sunshine_duration_s": TARGET[0], "dew_point_c": TARGET[1], "temp_c": TARGET[2],
                         "precipitation_mm": TARGET[3], "condition": ""})
        d += timedelta(days=1)
    with owner_engine.begin() as conn:
        conn.execute(WeatherHistory.__table__.insert(), rows)
    with Session(owner_engine) as s:
        s.add(HistoricalAnnualYield(block_id=None, season_year=2005, kg=59_003))
        s.commit()
    a = next(a for a in frozen.get("/api/estimate/analogs").json()["analogs"] if a["year"] == 2005)
    assert a["distance"] == 0 and a["factors"]["flowering_sun"]["z"] == 0
