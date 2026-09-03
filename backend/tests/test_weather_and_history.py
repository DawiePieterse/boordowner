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
def test_current_weather_does_not_hold_boord_open(client, manager_headers, farm_has_gps, monkeypatch):
    seen = {}

    def fake_fetch(lat, lon):
        seen["live_during_fetch"] = counter.live
        return {"temp": 21.0, "humidity": 50, "condition": "Clear"}

    monkeypatch.setattr(weather_router, "fetch_weather_cached", fake_fetch)
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.get("/api/weather/current", headers=manager_headers)
    assert r.status_code == 200 and r.json()["temp"] == 21.0
    assert seen["live_during_fetch"] == 0


def test_weather_history_sync_does_not_hold_boord_open(client, manager_headers, farm_has_gps, monkeypatch):
    seen = {}

    def fake_fetch(lat, lon, start_date, end_date, timeout=120):
        seen["live_during_fetch"] = counter.live
        return _hourly_payload(datetime(2026, 3, 1), 24)

    monkeypatch.setattr(weather_module, "fetch_historical_hourly", fake_fetch)
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.get("/api/weather/history", headers=manager_headers)
    assert r.status_code == 200
    assert seen["live_during_fetch"] == 0
    # and the sync actually stored what it fetched
    with Session(owner_engine) as s:
        assert s.exec(select(WeatherHistory)).first() is not None


def test_risk_forecast_does_not_hold_boord_open(client, manager_headers, farm_has_gps, monkeypatch):
    seen = {}

    def fake_forecast(lat, lon, days=16, timeout=30):
        seen["live_during_fetch"] = counter.live
        return _hourly_payload(datetime.now().replace(minute=0, second=0, microsecond=0), 24 * 16)

    monkeypatch.setattr(risk_module, "fetch_forecast_hourly", fake_forecast)
    monkeypatch.setattr(risk_module, "sync_recent_weather", lambda owner, boord: {"synced": 0})
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.get("/api/risk/forecast", headers=manager_headers)
    assert r.status_code == 200
    assert seen["live_during_fetch"] == 0
    assert r.json()["forecast_unavailable"] is False


def test_backfill_does_not_hold_boord_open(client, manager_headers, farm_has_gps, monkeypatch):
    seen = {}

    def fake_range(lat, lon, start, end):
        seen["live_during_fetch"] = counter.live
        return weather_module.parse_hourly_rows(_hourly_payload(datetime(2026, 1, 1), 48), lat, lon)

    monkeypatch.setattr(weather_router, "fetch_hourly_range", fake_range)
    with _OpenConnectionCounter(boord_engine) as counter:
        r = client.post("/api/weather/history/backfill?years=1", headers=manager_headers)
    assert r.status_code == 200, r.text
    assert seen["live_during_fetch"] == 0
    assert r.json()["imported"] == 48


def test_forecast_still_reads_boord_after_releasing(client, manager_headers, farm_has_gps, monkeypatch):
    """build_harvest_forecast releases Boord inside sync_recent_weather and
    then needs it again for _compute_driver_state. A closed SQLModel Session
    has to reopen transparently, or the whole tab 500s."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: _hourly_payload(datetime(2026, 3, 1), 24))
    monkeypatch.setattr(risk_module, "fetch_forecast_hourly",
                        lambda *a, **k: _hourly_payload(datetime.now(), 24))
    r = client.get("/api/risk/forecast", headers=manager_headers)
    assert r.status_code == 200
    assert r.json()["current_year"] == 2026   # came from Boord's SystemSetting


# --------------------------------------------------------------------------- #
# Graceful degradation
# --------------------------------------------------------------------------- #
def test_no_farm_location_is_reported_not_raised(client, manager_headers):
    """The fixture farm has no GPS. Nothing may invent one."""
    assert client.get("/api/weather/current", headers=manager_headers).json() == {"no_location": True}
    r = client.post("/api/weather/history/backfill", headers=manager_headers)
    assert r.status_code == 200 and r.json() == {"no_location": True, "imported": 0}


def test_forecast_survives_an_unreachable_weather_service(client, manager_headers, farm_has_gps, monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(risk_module, "fetch_forecast_hourly", boom)
    monkeypatch.setattr(risk_module, "sync_recent_weather", lambda owner, boord: {"synced": 0})
    r = client.get("/api/risk/forecast", headers=manager_headers)
    assert r.status_code == 200
    assert r.json()["forecast_unavailable"] is True


def test_backfill_leaves_history_alone_when_the_fetch_fails(client, manager_headers, farm_has_gps, monkeypatch):
    """A half-deleted weather table is worse than no import."""
    monkeypatch.setattr(weather_router, "fetch_hourly_range",
                        lambda *a, **k: weather_module.parse_hourly_rows(
                            _hourly_payload(datetime(2026, 1, 1), 24), -34.0, 18.5))
    client.post("/api/weather/history/backfill?years=1", headers=manager_headers)
    with Session(owner_engine) as s:
        before = len(s.exec(select(WeatherHistory)).all())
    assert before == 24

    def boom(*a, **k):
        raise OSError("dropped mid-download")
    monkeypatch.setattr(weather_router, "fetch_hourly_range", boom)
    r = client.post("/api/weather/history/backfill?years=1", headers=manager_headers)
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


def test_historical_harvest_import_replaces_the_table(client, manager_headers):
    r = client.post("/api/historical-harvest/import", headers=manager_headers,
                    files=_csv("block_id,date,kg,estimated\n7,2021-10-01,100.5,false\n"
                               "8a,2021-10-02,50,true\n"))
    assert r.status_code == 200 and r.json()["imported"] == 2
    with Session(owner_engine) as s:
        assert len(s.exec(select(HistoricalHarvest)).all()) == 2

    # a second import replaces rather than appends
    r = client.post("/api/historical-harvest/import", headers=manager_headers,
                    files=_csv("block_id,date,kg\n7,2022-10-01,7\n"))
    assert r.json()["imported"] == 1
    with Session(owner_engine) as s:
        rows = s.exec(select(HistoricalHarvest)).all()
    assert len(rows) == 1 and rows[0].season_year == 2022  # derived from the date


def test_historical_import_refuses_a_headings_only_file(client, manager_headers):
    client.post("/api/historical-harvest/import", headers=manager_headers,
                files=_csv("block_id,date,kg\n7,2021-10-01,100\n"))
    r = client.post("/api/historical-harvest/import", headers=manager_headers,
                    files=_csv("block_id,date,kg\n"))
    assert r.status_code == 400 and "no data rows" in r.json()["detail"]
    with Session(owner_engine) as s:
        assert len(s.exec(select(HistoricalHarvest)).all()) == 1  # nothing was wiped


def test_historical_import_refuses_a_mostly_unreadable_file(client, manager_headers):
    r = client.post("/api/historical-harvest/import", headers=manager_headers,
                    files=_csv("block_id,date,kg\n7,not-a-date,1\nx,nope,2\n9,2021-10-01,3\n"))
    assert r.status_code == 400
    assert "could not be read" in r.json()["detail"]


def test_annual_yield_import_allows_a_whole_farm_row(client, manager_headers):
    r = client.post("/api/historical-annual-yield/import", headers=manager_headers,
                    files=_csv("season_year,kg,block_id\n1995,50000,\n2013,1200,7\n"))
    assert r.status_code == 200 and r.json()["imported"] == 2
    with Session(owner_engine) as s:
        rows = {r.season_year: r.block_id for r in s.exec(select(HistoricalAnnualYield)).all()}
    assert rows == {1995: None, 2013: "7"}


def test_historical_imports_are_manager_only(client, make_user):
    _uid, viewer = make_user("importer")
    for path in ("/api/historical-harvest/import", "/api/historical-annual-yield/import"):
        r = client.post(path, headers=viewer, files=_csv("season_year,kg\n2020,1\n"))
        assert r.status_code == 403


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


def test_history_returns_only_the_latest_year_by_default(client, manager_headers, farm_has_gps,
                                                          monkeypatch):
    """The whole record is ~14,500 daily points and several MB; the chart
    opens on one year. Returning everything made every tab open pay for
    forty years of data to draw one."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2019, 2024, 2025)
    body = client.get("/api/weather/history", headers=manager_headers).json()

    assert body["years_returned"] == [2025], "opens on the most recent year on file"
    assert {p["year"] for p in body["points"]} == {2025}
    # ...but every year stays tickable, or the filter row would shrink to
    # whatever happened to be charted.
    assert body["years"] == [2019, 2024, 2025]


def test_history_returns_exactly_the_years_asked_for(client, manager_headers, farm_has_gps,
                                                      monkeypatch):
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2019, 2024, 2025)

    body = client.get("/api/weather/history?years=2019,2025", headers=manager_headers).json()
    assert body["years_returned"] == [2019, 2025]
    assert {p["year"] for p in body["points"]} == {2019, 2025}
    assert body["years"] == [2019, 2024, 2025], "the filter list is unaffected by the selection"


def test_history_ignores_years_that_are_not_on_file(client, manager_headers, farm_has_gps,
                                                     monkeypatch):
    """A stale tab asking for a year since removed is a stale tab, not a bad
    request - it must not 4xx the whole chart."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2024, 2025)

    r = client.get("/api/weather/history?years=1901,2025,notayear", headers=manager_headers)
    assert r.status_code == 200
    assert r.json()["years_returned"] == [2025]


def test_history_falls_back_to_the_default_when_no_year_asked_for_is_on_file(
        client, manager_headers, farm_has_gps, monkeypatch):
    """Dropping every year asked for must not fall through to "no year
    filter", which is the whole record - a stale tab asking only for a year
    since removed would be answered with everything, the very thing the
    parameter exists to stop."""
    monkeypatch.setattr(weather_module, "fetch_historical_hourly",
                        lambda *a, **k: {"hourly": {"time": [], }})
    _seed_years(2019, 2024, 2025)

    body = client.get("/api/weather/history?years=1901", headers=manager_headers).json()
    assert body["years_returned"] == [2025], "the same default a fresh tab gets"
    assert {p["year"] for p in body["points"]} == {2025}
