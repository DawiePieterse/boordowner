"""The farm's own on-site iWeathar station: HTML scrape + blend into
fetch_weather(). Network is stubbed throughout, same style as
test_weather_and_history.py.

_LIVE_PAGE below is the two data-bearing table rows copied verbatim from a
real fetch of https://iweathar.co.za/display?s_id=2235 (iWeathar Station
Bekfontein) on 2026-09-22, wrapped in just enough HTML for the parser to see
the same surrounding markup - mismatched quotes, inconsistent tag case, a
duplicated `class='numbers'` attribute - rather than a clean hand-written
fixture that would miss those quirks.
"""
import config
import weather as weather_module

_LIVE_PAGE = """
<HTML><BODY>
<TR><TD nowrap valign='top'><FONT COLOR='#FFFFFF'>Last Update:</FONT></TD><TD valign='top'>2026-09-22 09:10:43</TD></TR>
<TR BGCOLOR='#86A751' align='left'><TD><font color='#FFFFFF'>Wind Speed:</font></TD><TD nowrap><a href='http://iweathar.co.za/windgraph?s_id=2235&updated=2026-09-22+09:10:43&unit=kmh' target='_NEW' class='numbers'>0|4|8</a> <font class='units'>kmh</font></TD><TD nowrap><font color='#FFFFFF'>Wind Direction:</font></TD><TD nowrap><a href='http://iweathar.co.za/compass_graph?s_id=2235&updated=2026-09-22+09:10:43' target='_NEW' class='numbers'>WSW</a> <font class='numbers'><font class='units'>252&deg;</font></font></TD><TD><font color='#FFFFFF'>Temperature:</font></TD><TD><a href='http://iweathar.co.za/temp_graph?s_id=2235&updated=2026-09-22+09:10:43' target='_NEW' class='numbers'>20.2<font class='units'>&deg;C</font></a></TD></TR><TR align='left'><TD><font color='#FFFFFF'>Wet Bulb:</font></TD><TD><font class='numbers'>19.1</font><font class='units'>&deg;C</font></TD><TD><font color='#FFFFFF'>Discomfort:</font></TD><TD><a href='http://iweathar.co.za/discomfort_graph?s_id=2235&updated=2026-09-22+09:10:43' target='_NEW' class='numbers' title='90-100 - Very Uncomfortable, 100-110 - Extremely Uncomfortable, 110+ - Hazardous to Health'>83</a></TD><TD><font color='#FFFFFF'>Humidity:</font></TD><TD><a href='http://iweathar.co.za/hum_graph?s_id=2235&updated=2026-09-22+09:10:43' class='numbers' target='_NEW' border='0' class='numbers'>91</a><font class='units'>%</font></TD></TR><TR align='left'><TD><font color='#FFFFFF'>Rainfall Today:</font></TD><TD><a href='rain_graph?s_id=2235&fd=2026-09-22+00:00:00&td=2026-09-22+23:59:59' target='_NEW' border='0' class='numbers'>13.4</a><font class='units'>mm</font></TD><TD><font color='#FFFFFF'>12 hrs Rainfall:</font></TD><TD><a href='rain_graph?s_id=2235&fd=2026-09-21 21:12:21&td=2026-09-22+23:59:59' target='_NEW' border='0' class='numbers'>13.4</a><font class='units'>mm</font></TD><TD><font color='#FFFFFF'>24 hrs Rainfall:</font></TD><TD><a href='rain_graph?s_id=2235&fd=2026-09-21 09:12:21&td=2026-09-22+23:59:59' target='_NEW' border='0' class='numbers'>13.4</a><font class='units'>mm</font></TD></TR><TR align='left'><TD><font color='#FFFFFF'>Barometer:</font></TD><TD><a href='http://iweathar.co.za/press_graph?s_id=2235&updated=2026-09-22+09:10:43' target='_NEW' border='0' class='numbers'>983.1</a><font class='units'>mb</font></TD><TD><font color='#FFFFFF'>Dew Point:</font></TD><TD><font class='numbers'>18.7<font class='units'>&deg;C</font></font></TD><TD nowrap><font color='#FFFFFF'>Clouds AGL:</font></TD><TD><font class='numbers'>608</font><font class='units'>ft (185 m)</font></TD></TR>
<TR align='left'><TD width='100'><font color='white'>Wind Gust:</font></TD><TD><font class='numbers'>12</font> <font class='units'>km/h</font></TD><TD width='100'><font color='white'>Min Temp:</font></TD><TD><font class='numbers'>18.9</font> <font class='units'>&deg;C</font></TD><TD width='100'><font color='white'>Max Temp:</font></TD><TD><font class='numbers'>20.5</font> <font class='units'>&deg;C</font></TD></TR><TR><TD width='100'><font color='white'>Wind Average:</font></TD><TD><font class='numbers'>8</font> <font class='units'>km/h</font></TD><TD width='100'><font color='white'>Min Hum:</font></TD><TD><font class='numbers'>85</font> <font class='units'>%</font></TD><TD width='100'><font color='white'>Max Hum:</font></TD><TD><font class='numbers'>95</font> <font class='units'>%</font></TD></TR>
</BODY></HTML>
"""


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _stub_urlopen(monkeypatch, body: str, capture: dict = None):
    def fake(url, timeout=None):
        if capture is not None:
            capture["url"] = url
        return _FakeResponse(body.encode("iso-8859-1"))
    monkeypatch.setattr(weather_module.urllib.request, "urlopen", fake)


# --------------------------------------------------------------------------- #
# fetch_iweathar_current(): the scrape itself
# --------------------------------------------------------------------------- #
def test_parses_the_live_page(monkeypatch):
    capture = {}
    _stub_urlopen(monkeypatch, _LIVE_PAGE, capture)
    result = weather_module.fetch_iweathar_current("2235")

    assert capture["url"] == "https://iweathar.co.za/display?s_id=2235"
    assert result["temp"] == 20.2
    assert result["humidity"] == 91.0
    assert result["dew_point_c"] == 18.7
    assert result["pressure_mb"] == 983.1
    assert result["rain_today_mm"] == 13.4
    assert result["wind_gust_kmh"] == 12.0
    assert result["wind_avg_kmh"] == 8.0
    assert result["temp_min_c"] == 18.9
    assert result["temp_max_c"] == 20.5
    assert result["source"] == "iweathar"
    # 13.4mm today is real, measured rain - stronger than a forecast model's
    # cloud-code guess, so it becomes the condition outright.
    assert result["condition"] == "Heavy Rain"


def test_condition_from_rain_today_thresholds():
    f = weather_module._condition_from_rain_today_mm
    assert f(0) is None
    assert f(1.5) == "Drizzle"
    assert f(2) == "Drizzle"
    assert f(5) == "Rain"
    assert f(10) == "Rain"
    assert f(10.1) == "Heavy Rain"


def test_returns_empty_when_the_page_has_no_readings(monkeypatch):
    _stub_urlopen(monkeypatch, "<HTML><BODY>Station offline</BODY></HTML>")
    assert weather_module.fetch_iweathar_current("2235") == {}


def test_returns_empty_when_the_station_is_unreachable(monkeypatch):
    def boom(url, timeout=None):
        raise OSError("network down")
    monkeypatch.setattr(weather_module.urllib.request, "urlopen", boom)
    assert weather_module.fetch_iweathar_current("2235") == {}


# --------------------------------------------------------------------------- #
# fetch_weather(): blending the station into the header's current-conditions
# --------------------------------------------------------------------------- #
def test_fetch_weather_is_open_meteo_only_when_no_station_is_configured(monkeypatch):
    monkeypatch.setattr(config, "IWEATHAR_STATION_ID", None)
    calls = []
    monkeypatch.setattr(weather_module, "fetch_iweathar_current",
                        lambda *a, **k: calls.append(1) or {})
    monkeypatch.setattr(weather_module, "_fetch_open_meteo_current",
                        lambda lat, lon: {"temp": 19.0, "humidity": 60, "condition": "Clear"})
    result = weather_module.fetch_weather(-25.57, 31.59)
    assert calls == []  # never even asked - see config.IWEATHAR_STATION_ID's comment
    assert result == {"temp": 19.0, "humidity": 60, "condition": "Clear"}


def test_fetch_weather_prefers_the_station_reading(monkeypatch):
    monkeypatch.setattr(config, "IWEATHAR_STATION_ID", "2235")
    monkeypatch.setattr(weather_module, "fetch_iweathar_current",
                        lambda *a, **k: {"temp": 20.2, "humidity": 91.0,
                                        "condition": "Heavy Rain", "rain_today_mm": 13.4,
                                        "source": "iweathar"})
    monkeypatch.setattr(weather_module, "_fetch_open_meteo_current",
                        lambda lat, lon: {"temp": 22.0, "humidity": 55, "condition": "Clear"})
    result = weather_module.fetch_weather(-25.57, 31.59)
    # The real gauge's numbers win outright, including the condition its own
    # rain reading implies - not Open-Meteo's "Clear" for the same moment.
    assert result["temp"] == 20.2
    assert result["humidity"] == 91.0
    assert result["condition"] == "Heavy Rain"
    assert result["rain_today_mm"] == 13.4
    assert result["source"] == "iweathar"


def test_fetch_weather_fills_gaps_from_open_meteo(monkeypatch):
    """The station has no sky sensor, so it never reports a condition when
    it hasn't rained - Open-Meteo is the only source for that field."""
    monkeypatch.setattr(config, "IWEATHAR_STATION_ID", "2235")
    monkeypatch.setattr(weather_module, "fetch_iweathar_current",
                        lambda *a, **k: {"temp": 18.0, "humidity": 70.0, "source": "iweathar"})
    monkeypatch.setattr(weather_module, "_fetch_open_meteo_current",
                        lambda lat, lon: {"temp": 17.5, "humidity": 68, "condition": "Partly Cloudy"})
    result = weather_module.fetch_weather(-25.57, 31.59)
    assert result["temp"] == 18.0        # station reading kept
    assert result["humidity"] == 70.0    # station reading kept
    assert result["condition"] == "Partly Cloudy"   # only Open-Meteo has this


def test_fetch_weather_falls_back_fully_when_the_station_is_down(monkeypatch):
    monkeypatch.setattr(config, "IWEATHAR_STATION_ID", "2235")
    monkeypatch.setattr(weather_module, "fetch_iweathar_current", lambda *a, **k: {})
    monkeypatch.setattr(weather_module, "_fetch_open_meteo_current",
                        lambda lat, lon: {"temp": 21.0, "humidity": 50, "condition": "Overcast"})
    result = weather_module.fetch_weather(-25.57, 31.59)
    assert result == {"temp": 21.0, "humidity": 50, "condition": "Overcast"}


def test_fetch_weather_returns_empty_when_both_sources_fail(monkeypatch):
    monkeypatch.setattr(config, "IWEATHAR_STATION_ID", "2235")
    monkeypatch.setattr(weather_module, "fetch_iweathar_current", lambda *a, **k: {})
    monkeypatch.setattr(weather_module, "_fetch_open_meteo_current", lambda lat, lon: {})
    assert weather_module.fetch_weather(-25.57, 31.59) == {}
