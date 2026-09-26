"""Weather and historical-import endpoints, with the network stubbed.

The Open-Meteo calls are replaced by fakes that record what the rest of the
app looked like at the moment they ran - which is how the "Boord's database
is not held open across a fetch" rule is actually enforced rather than just
documented.
"""
import io
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

import routers.risk as risk_module
import routers.weather as weather_router
import weather as weather_module
from db import boord_engine, owner_engine
from models_boord import SystemSetting
from models_owner import HistoricalAnnualYield, HistoricalHarvest, WeatherHistory
from tests.test_boord_isolation import _OpenConnectionCounter


@pytest.fixture()
def farm_has_gps():
    """The fixture farm ships with no GPS (weather is inert without one).
    Set one for the tests that need the weather paths to actually run, and
    put it back afterwards - boord.db is shared across the suite.

    Written through a separate read-write engine on purpose: the app's own
    boord_engine refuses writes, which is the point of it.
    """
    from sqlalchemy import create_engine
    import config
    rw = create_engine(f"sqlite:///{config.BOORD_DB_PATH}")
    with Session(rw) as s:
        row = s.get(SystemSetting, 1)
        row.gps_lat, row.gps_lon = -34.0, 18.5
        s.add(row)
        s.commit()
    yield (-34.0, 18.5)
    with Session(rw) as s:
        row = s.get(SystemSetting, 1)
        row.gps_lat, row.gps_lon = None, None
        s.add(row)
        s.commit()
    rw.dispose()
    weather_module._cache.clear()   # don't leak a cached reading into later tests


def _hourly_payload(start: datetime, hours: int) -> dict:
    times = [(start + timedelta(hours=h)).isoformat(timespec="minutes") for h in range(hours)]
    return {"hourly": {
        "time": times,
        "temperature_2m": [20.0] * hours,
        "relative_humidity_2m": [55.0] * hours,
        "dew_point_2m": [10.0] * hours,
        "precipitation": [0.0] * hours,
        "weather_code": [0] * hours,
        "wind_speed_10m": [5.0] * hours,
        "soil_temperature_6cm": [18.0] * hours,
        "uv_index": [3.0] * hours,
        "sunshine_duration": [3600.0] * hours,
    }}


# --------------------------------------------------------------------------- #
# The rule: no Boord connection open while we are talking to Open-Meteo
# --------------------------------------------------------------------------- #
def test_current_weather_does_not_hold_boord_open(client, farm_has_gps, monkeypatch):
    seen = {}

    def fake_fetch(lat, lon):
        seen["live_during_fetch"] = counter.live
        return {"temp": 21.0, "humidity": 50, "condition": "Clear"}

    monkeypatch.setattr(weather_router, "fetch_weather_cached", fake_fetch)
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.get("/api/weather/current")
    assert r.status_code == 200 and r.json()["temp"] == 21.0
    assert seen["live_during_fetch"] == 0


def test_weather_history_sync_does_not_hold_boord_open(client, farm_has_gps, monkeypatch):
    seen = {}

    def fake_fetch(lat, lon, start_date, end_date, timeout=120):
        seen["live_during_fetch"] = counter.live
        return _hourly_payload(datetime(2026, 3, 1), 24)

    monkeypatch.setattr(weather_module, "fetch_historical_hourly", fake_fetch)
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.get("/api/weather/history")
    assert r.status_code == 200
    assert seen["live_during_fetch"] == 0
    # and the sync actually stored what it fetched
    with Session(owner_engine) as s:
        assert s.exec(select(WeatherHistory)).first() is not None


def test_risk_forecast_does_not_hold_boord_open(client, farm_has_gps, monkeypatch):
    seen = {}

    def fake_forecast(lat, lon, days=16, timeout=30):
        seen["live_during_fetch"] = counter.live
        return _hourly_payload(datetime.now().replace(minute=0, second=0, microsecond=0), 24 * 16)

    monkeypatch.setattr(risk_module, "fetch_forecast_hourly", fake_forecast)
    monkeypatch.setattr(risk_module, "sync_recent_weather", lambda owner, boord: {"synced": 0})
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.get("/api/risk/forecast")
    assert r.status_code == 200
    assert seen["live_during_fetch"] == 0
    assert r.json()["forecast_unavailable"] is False


def test_backfill_does_not_hold_boord_open(client, farm_has_gps, monkeypatch):
    seen = {}

    def fake_range(lat, lon, start, end):
        seen["live_during_fetch"] = counter.live
        return weather_module.parse_hourly_rows(_hourly_payload(datetime(2026, 1, 1), 48), lat, lon)

    monkeypatch.setattr(weather_router, "fetch_hourly_range", fake_range)
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.post("/api/weather/history/backfill?years=1")
    assert r.status_code == 200, r.text
    assert seen["live_during_fetch"] == 0
    assert r.json()["imported"] == 48


def test_forecast_still_reads_boord_after_releasing(client, farm_has_gps, monkeypatch):
    """build_harvest_forecast releases Boord inside sync_recent_weather and
    then needs it again for _compute_driver_state. A closed SQLModel Session
    has to reopen transparently, or the whole tab 500s."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: _hourly_payload(datetime(2026, 3, 1), 24))
    monkeypatch.setattr(risk_module, "fetch_forecast_hourly",
                        lambda *a, **k: _hourly_payload(datetime.now(), 24))
    r = client.get("/api/risk/forecast")
    assert r.status_code == 200
    assert r.json()["current_year"] == 2026   # came from Boord's SystemSetting


# --------------------------------------------------------------------------- #
# Graceful degradation
# --------------------------------------------------------------------------- #
def test_no_farm_location_is_reported_not_raised(client):
    """The fixture farm has no GPS. Nothing may invent one."""
    assert client.get("/api/weather/current").json() == {"no_location": True}
    r = client.post("/api/weather/history/backfill")
    assert r.status_code == 200 and r.json() == {"no_location": True, "imported": 0}


def test_forecast_survives_an_unreachable_weather_service(client, farm_has_gps, monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(risk_module, "fetch_forecast_hourly", boom)
    monkeypatch.setattr(risk_module, "sync_recent_weather", lambda owner, boord: {"synced": 0})
    r = client.get("/api/risk/forecast")
    assert r.status_code == 200
    assert r.json()["forecast_unavailable"] is True


def test_backfill_leaves_history_alone_when_the_fetch_fails(client, farm_has_gps, monkeypatch):
    """A half-deleted weather table is worse than no import."""
    monkeypatch.setattr(weather_router, "fetch_hourly_range",
                        lambda *a, **k: weather_module.parse_hourly_rows(
                            _hourly_payload(datetime(2026, 1, 1), 24), -34.0, 18.5))
    client.post("/api/weather/history/backfill?years=1")
    with Session(owner_engine) as s:
        before = len(s.exec(select(WeatherHistory)).all())
    assert before == 24

    def boom(*a, **k):
        raise OSError("dropped mid-download")
    monkeypatch.setattr(weather_router, "fetch_hourly_range", boom)
    r = client.post("/api/weather/history/backfill?years=1")
    assert r.status_code == 502
    with Session(owner_engine) as s:
        assert len(s.exec(select(WeatherHistory)).all()) == before


def test_sync_never_raises_when_the_service_is_down(farm_has_gps, monkeypatch):
    """The Weather tab must still render its stored history."""
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(weather_module, "fetch_historical_hourly", boom)
    with Session(owner_engine) as owner, Session(boord_engine) as boord:
        assert weather_module.sync_recent_weather(owner, boord) == {"synced": 0, "error": True}


# --------------------------------------------------------------------------- #
# Historical imports
# --------------------------------------------------------------------------- #
def _csv(text):
    return {"file": ("history.csv", io.BytesIO(text.encode()), "text/csv")}


def test_historical_harvest_import_replaces_the_table(client):
    r = client.post("/api/historical-harvest/import",
                    files=_csv("block_id,date,kg,estimated\n7,2021-10-01,100.5,false\n"
                               "8a,2021-10-02,50,true\n"))
    assert r.status_code == 200 and r.json()["imported"] == 2
    with Session(owner_engine) as s:
        assert len(s.exec(select(HistoricalHarvest)).all()) == 2

    # a second import replaces rather than appends
    r = client.post("/api/historical-harvest/import",
                    files=_csv("block_id,date,kg\n7,2022-10-01,7\n"))
    assert r.json()["imported"] == 1
    with Session(owner_engine) as s:
        rows = s.exec(select(HistoricalHarvest)).all()
    assert len(rows) == 1 and rows[0].season_year == 2022  # derived from the date


def test_historical_import_refuses_a_headings_only_file(client):
    client.post("/api/historical-harvest/import",
                files=_csv("block_id,date,kg\n7,2021-10-01,100\n"))
    r = client.post("/api/historical-harvest/import",
                    files=_csv("block_id,date,kg\n"))
    assert r.status_code == 400 and "no data rows" in r.json()["detail"]
    with Session(owner_engine) as s:
        assert len(s.exec(select(HistoricalHarvest)).all()) == 1  # nothing was wiped


def test_historical_import_refuses_a_mostly_unreadable_file(client):
    r = client.post("/api/historical-harvest/import",
                    files=_csv("block_id,date,kg\n7,not-a-date,1\nx,nope,2\n9,2021-10-01,3\n"))
    assert r.status_code == 400
    assert "could not be read" in r.json()["detail"]


def test_annual_yield_import_allows_a_whole_farm_row(client):
    r = client.post("/api/historical-annual-yield/import",
                    files=_csv("season_year,kg,block_id\n1995,50000,\n2013,1200,7\n"))
    assert r.status_code == 200 and r.json()["imported"] == 2
    with Session(owner_engine) as s:
        rows = {r.season_year: r.block_id for r in s.exec(select(HistoricalAnnualYield)).all()}
    assert rows == {1995: None, 2013: "7"}


# --------------------------------------------------------------------------- #
# The Weather tab fetches a year at a time
# --------------------------------------------------------------------------- #
def _seed_years(*years):
    """One noon hour on 1 July of each given year, at the farm_has_gps
    fixture's coordinates so none of it counts as another location's."""
    with Session(owner_engine) as s:
        for y in years:
            s.add(WeatherHistory(timestamp=datetime(y, 7, 1, 12), temp_c=20.0 + y % 10,
                                 lat=-34.0, lon=18.5))
        s.commit()


def test_history_returns_only_the_latest_year_by_default(client, farm_has_gps,
                                                          monkeypatch):
    """The whole record is ~14,500 daily points and several MB; the chart
    opens on one year. Returning everything made every tab open pay for
    forty years of data to draw one."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2019, 2024, 2025)
    body = client.get("/api/weather/history").json()

    assert body["years_returned"] == [2025], "opens on the most recent year on file"
    assert {p["year"] for p in body["points"]} == {2025}
    # ...but every year stays tickable, or the filter row would shrink to
    # whatever happened to be charted.
    assert body["years"] == [2019, 2024, 2025]


def test_history_returns_exactly_the_years_asked_for(client, farm_has_gps,
                                                      monkeypatch):
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2019, 2024, 2025)

    body = client.get("/api/weather/history?years=2019,2025").json()
    assert body["years_returned"] == [2019, 2025]
    assert {p["year"] for p in body["points"]} == {2019, 2025}
    assert body["years"] == [2019, 2024, 2025], "the filter list is unaffected by the selection"


def test_history_ignores_years_that_are_not_on_file(client, farm_has_gps,
                                                     monkeypatch):
    """A stale tab asking for a year since removed is a stale tab, not a bad
    request - it must not 4xx the whole chart."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2024, 2025)

    r = client.get("/api/weather/history?years=1901,2025,notayear")
    assert r.status_code == 200
    assert r.json()["years_returned"] == [2025]


def test_history_falls_back_to_the_default_when_no_year_asked_for_is_on_file(
        client, farm_has_gps, monkeypatch):
    """Dropping every year asked for must not fall through to "no year
    filter", which is the whole record - a stale tab asking only for a year
    since removed would be answered with everything, the very thing the
    parameter exists to stop."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2019, 2024, 2025)

    body = client.get("/api/weather/history?years=1901").json()
    assert body["years_returned"] == [2025], "the same default a fresh tab gets"
    assert {p["year"] for p in body["points"]} == {2025}


# --------------------------------------------------------------------------- #
# Daily aggregation values (build_weather_history)
# --------------------------------------------------------------------------- #
def test_history_daily_aggregates_are_the_right_statistic(client, farm_has_gps, monkeypatch):
    """Each metric in routers/weather._METRICS is a different statistic over
    the day's 24 hours - mean, daily max/min, sum, seconds->hours. One day
    of known hourly values pins every one of them."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": []}})
    with Session(owner_engine) as s:
        for h in range(24):
            s.add(WeatherHistory(timestamp=datetime(2025, 7, 1, h), temp_c=10.0 + h,
                                 precipitation_mm=0.5, sunshine_duration_s=1800.0,
                                 uv_index=float(h % 5), lat=-34.0, lon=18.5))
        s.commit()
    body = client.get("/api/weather/history?years=2025").json()
    assert len(body["points"]) == 1
    p = body["points"][0]
    assert p["date"] == "2025-07-01"
    assert p["temp_c"] == 21.5          # mean of 10..33
    assert p["temp_max_c"] == 33.0
    assert p["temp_min_c"] == 10.0
    assert p["precipitation_mm"] == 12.0  # 24 x 0.5, summed
    assert p["sunshine_hours"] == 12.0    # 24 x 1800s -> hours
    assert p["uv_index"] == 4.0           # the day's peak, not its mean
    assert {m["key"] for m in body["metrics"]} >= {"temp_c", "temp_max_c", "temp_min_c"}
    # The catch-up that ran first reports what it did: nothing to add (the
    # fake answers no hours), and a gap far wider than one call covers.
    assert body["sync"] == {"synced": 0, "complete": False}


# --------------------------------------------------------------------------- #
# sync_recent_weather happy paths
# --------------------------------------------------------------------------- #
def _sync():
    with Session(owner_engine) as owner, Session(boord_engine) as boord:
        return weather_module.sync_recent_weather(owner, boord)


def _recording_fetch(monkeypatch, hours_per_call=24):
    """Stands in for fetch_historical_hourly: records each (start, end) it
    was asked for and answers with `hours_per_call` hours from `start`."""
    calls = []

    def fake(lat, lon, start_date, end_date, timeout=120):
        calls.append((start_date, end_date))
        return _hourly_payload(datetime.fromisoformat(start_date), hours_per_call)
    monkeypatch.setattr(weather_module, "fetch_historical_hourly", fake)
    return calls


def test_sync_skips_the_fetch_when_the_current_hour_is_already_stored(client, farm_has_gps, monkeypatch):
    """Data is hourly, so a row in the current hour means nothing newer
    exists to fetch - that is the whole throttle on repeat tab opens."""
    calls = _recording_fetch(monkeypatch)
    this_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
    with Session(owner_engine) as s:
        s.add(WeatherHistory(timestamp=this_hour, temp_c=20.0, lat=-34.0, lon=18.5))
        s.commit()
    assert _sync() == {"synced": 0}
    assert calls == []


def test_sync_refuses_to_append_to_another_locations_history(client, farm_has_gps, monkeypatch):
    calls = _recording_fetch(monkeypatch)
    with Session(owner_engine) as s:
        s.add(WeatherHistory(timestamp=datetime.now() - timedelta(days=3), temp_c=20.0,
                             lat=-20.0, lon=25.0))   # not where the farm is now
        s.commit()
    assert _sync() == {"synced": 0, "location_changed": True}
    assert calls == []


def test_sync_appends_only_hours_newer_than_the_latest_stored(client, farm_has_gps, monkeypatch):
    """The fetch starts on the latest stored DAY (the API is day-granular),
    so it returns hours already on file; only the newer ones may land."""
    calls = _recording_fetch(monkeypatch, hours_per_call=24)
    latest = (datetime.now() - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    with Session(owner_engine) as s:
        s.add(WeatherHistory(timestamp=latest, temp_c=20.0, lat=-34.0, lon=18.5))
        s.commit()
    result = _sync()
    # The fake answers 24 hours from the latest stored day's midnight: hours
    # 00:00-06:00 are already on file (7 rows), 07:00-23:00 are new.
    assert result == {"synced": 17}
    assert calls[0][0] == latest.date().isoformat()
    with Session(owner_engine) as s:
        rows = s.exec(select(WeatherHistory).order_by(WeatherHistory.timestamp)).all()
        assert len(rows) == 18
        assert rows[0].timestamp == latest
        assert all(r.timestamp > latest for r in rows[1:])


def test_sync_catches_up_a_long_gap_in_bounded_chunks(client, farm_has_gps, monkeypatch):
    """A server that has been off for months used to ask for the whole gap
    in one short-timeout call, which never returned in time - so the gap
    never closed. It is fetched in SYNC_CHUNK_DAYS slices, a few per call,
    each landing before the next is asked for."""
    calls = _recording_fetch(monkeypatch, hours_per_call=24)
    latest = (datetime.now() - timedelta(days=100)).replace(hour=23, minute=0, second=0, microsecond=0)
    with Session(owner_engine) as s:
        s.add(WeatherHistory(timestamp=latest, temp_c=20.0, lat=-34.0, lon=18.5))
        s.commit()
    result = _sync()
    assert len(calls) == weather_module.SYNC_MAX_CHUNKS_PER_CALL
    spans = [(datetime.fromisoformat(e) - datetime.fromisoformat(s)).days + 1 for s, e in calls]
    assert all(span == weather_module.SYNC_CHUNK_DAYS for span in spans)
    # Consecutive, gap-free slices starting on the latest stored day.
    assert calls[0][0] == latest.date().isoformat()
    for (_, end), (next_start, _) in zip(calls, calls[1:]):
        assert datetime.fromisoformat(next_start).date() == datetime.fromisoformat(end).date() + timedelta(days=1)
    # 3 x 31 days < 100: more to come on the next tab open, and it says so.
    assert result["complete"] is False
    assert result["synced"] > 0


def test_sync_keeps_what_landed_before_a_failure_and_backs_off(client, farm_has_gps, monkeypatch):
    calls = []

    def flaky(lat, lon, start_date, end_date, timeout=120):
        calls.append(start_date)
        if len(calls) == 2:
            raise OSError("network down")
        return _hourly_payload(datetime.fromisoformat(start_date), 24)
    monkeypatch.setattr(weather_module, "fetch_historical_hourly", flaky)
    latest = (datetime.now() - timedelta(days=100)).replace(hour=0, minute=0, second=0, microsecond=0)
    with Session(owner_engine) as s:
        s.add(WeatherHistory(timestamp=latest, temp_c=20.0, lat=-34.0, lon=18.5))
        s.commit()
    result = _sync()
    assert result == {"synced": 23, "error": True}   # slice 1's 23 new hours stayed
    with Session(owner_engine) as s:
        assert len(s.exec(select(WeatherHistory)).all()) == 24
    # ...and the next call inside the backoff window doesn't touch the network.
    assert _sync() == {"synced": 0, "error": True}
    assert len(calls) == 2
    weather_module._sync_failed_until = 0.0


# --------------------------------------------------------------------------- #
# The whole-table figures /history reports are cached until the table changes
# --------------------------------------------------------------------------- #
def test_history_reuses_its_whole_table_figures_until_the_table_changes(
        client, farm_has_gps, monkeypatch):
    """The years on file and the foreign-row count are each a full scan of
    WeatherHistory. They are reused across loads, and dropped the moment the
    table changes - by this process's own append, or by a write from
    anywhere else (an import script), which only the validity key sees."""
    scans = []
    real_count = weather_router.foreign_row_count

    def counting(*args):
        scans.append(args)
        return real_count(*args)
    monkeypatch.setattr(weather_router, "foreign_row_count", counting)
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": []}})
    yesterday = (datetime.now() - timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    with Session(owner_engine) as s:
        s.add(WeatherHistory(timestamp=datetime(2024, 7, 1, 12), temp_c=20.0,
                             lat=-20.0, lon=25.0))   # somewhere else
        s.add(WeatherHistory(timestamp=yesterday, temp_c=20.0, lat=-34.0, lon=18.5))
        s.commit()

    first = client.get("/api/weather/history").json()
    second = client.get("/api/weather/history").json()
    assert second == first, "a cached figure must not change the response"
    assert first["hours_elsewhere"] == 1
    assert first["years"] == sorted({2024, yesterday.year})
    assert len(scans) == 1, "the second load reused the first one's scan"

    # This process's own catch-up appends: invalidated explicitly.
    _recording_fetch(monkeypatch, hours_per_call=48)
    third = client.get("/api/weather/history").json()
    assert third["sync"]["synced"] > 0
    assert len(scans) == 2

    # A write the app never hears about, as an import script's would be.
    with Session(owner_engine) as s:
        s.add(WeatherHistory(timestamp=datetime(2019, 7, 1, 12), temp_c=20.0,
                             lat=-20.0, lon=25.0))
        s.commit()
    fourth = client.get("/api/weather/history").json()
    assert fourth["years"] == sorted({2019, 2024, yesterday.year})
    assert fourth["hours_elsewhere"] == 2
    assert len(scans) == 3
