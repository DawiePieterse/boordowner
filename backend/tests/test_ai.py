"""Ask about this estimate: provider plumbing (ai.py) and the summary the
model is given (routers/ai.py). No test reaches a real provider - the
network call is stood in for at ai._post_stream / ai._get_json."""
import json
import urllib.error
from datetime import datetime, timezone
from io import BytesIO

import pytest

import ai
import config
from db import boord_engine
from tests.test_boord_isolation import _OpenConnectionCounter
from tests.test_estimate import _seed_history
from tests.test_weather_and_history import farm_has_gps  # noqa: F401 - fixture


@pytest.fixture()
def gemini(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_API_KEY", "test-key")
    monkeypatch.setattr(config, "AI_ENDPOINT", "")
    monkeypatch.setattr(config, "AI_MODEL", "")
    ai._resolved.clear()
    yield
    ai._resolved.clear()


def _gemini_sse(*pieces):
    return [f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': p}]}}]})}\n".encode()
            for p in pieces]


def _http_error(status, message):
    body = json.dumps({"error": {"message": message}}).encode()
    return urllib.error.HTTPError("https://x", status, "err", {}, BytesIO(body))


def _ndjson(resp):
    return [json.loads(l) for l in resp.text.splitlines() if l.strip()]


# --------------------------------------------------------------------------- #
# Provider plumbing
# --------------------------------------------------------------------------- #
def test_off_without_a_key(monkeypatch, client):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")
    assert ai.settings() is None
    assert client.get("/api/ai/status").json() == {"configured": False}
    r = client.post("/api/ai/ask", json={"question": "Review this estimate"})
    assert r.status_code == 503
    # A custom endpoint needs its URL, not necessarily a key.
    monkeypatch.setattr(config, "AI_PROVIDER", "custom")
    monkeypatch.setattr(config, "AI_ENDPOINT", "http://localhost:11434/v1/chat/completions")
    assert ai.settings()["provider"] == "custom"


def test_build_request_shapes(gemini):
    msgs = [{"role": "system", "content": "rules"}, {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}]
    url, headers, body, delta = ai.build_request(msgs, ai.settings())
    assert url.endswith("/models/gemini-3.8-flash:streamGenerateContent?alt=sse")
    assert headers["x-goog-api-key"] == "test-key" and "Authorization" not in headers
    assert body["systemInstruction"] == {"parts": [{"text": "rules"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert delta({"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}) == "ab"

    s = {"provider": "groq", "api_key": "k", "endpoint": "", "model": ""}
    url, headers, body, delta = ai.build_request(msgs, s)
    assert url == ai.PROVIDERS["groq"]["endpoint"] and headers["Authorization"] == "Bearer k"
    assert body["model"] == "llama-3.3-70b-versatile" and body["stream"] is True
    assert body["messages"] == msgs
    assert delta({"choices": [{"delta": {"content": "x"}}]}) == "x"

    s = {"provider": "custom", "api_key": "", "endpoint": "http://h/v1/chat/completions", "model": "m"}
    url, headers, body, _ = ai.build_request(msgs, s)
    assert url == "http://h/v1/chat/completions" and "Authorization" not in headers and body["model"] == "m"


def test_model_helpers():
    assert ai.suggested_model("gemini-2.5-flash is no longer available. Please use models/gemini-3.8-flash") == "gemini-3.8-flash"
    assert ai.suggested_model("model not found") is None
    assert ai.model_gone("The model `llama-3.3-70b-versatile` has been decommissioned")
    assert not ai.model_gone("quota exceeded")
    p = ai.PROVIDERS["gemini"]
    ids = ["gemini-3.8-flash-image", "gemini-3.1-flash", "gemini-3.8-flash", "gemini-3.8-flash-lite",
           "gemini-3.9-flash-preview", "text-embedding-004"]
    assert ai.pick_model(ids, p["prefer"], p["avoid"]) == "gemini-3.8-flash"
    assert ai.pick_model(["gemini-3.8-flash-lite"], p["prefer"], p["avoid"]) == "gemini-3.8-flash-lite"
    assert ai.pick_model(["whisper-large"], ai.PROVIDERS["groq"]["prefer"], ai.PROVIDERS["groq"]["avoid"]) is None


def test_stream_follows_a_retired_model(gemini, monkeypatch):
    calls = []

    def fake_post(url, headers, body):
        calls.append(url)
        if "gemini-3.8-flash:" in url:
            raise _http_error(404, "models/gemini-3.8-flash is not found for API version v1beta")
        return iter(_gemini_sse("Hello", " there"))

    monkeypatch.setattr(ai, "_post_stream", fake_post)
    monkeypatch.setattr(ai, "_get_json", lambda url, headers: {"models": [
        {"name": "models/gemini-4.0-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-4.0-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]},
    ]})
    assert "".join(ai.stream([{"role": "user", "content": "q"}])) == "Hello there"
    assert "gemini-4.0-flash:" in calls[-1]
    # Remembered: the next question goes straight to the replacement.
    assert ai.model_for(ai.settings()) == "gemini-4.0-flash"
    calls.clear()
    list(ai.stream([{"role": "user", "content": "q"}]))
    assert len(calls) == 1


def test_stream_never_swaps_a_model_the_owner_chose(gemini, monkeypatch):
    monkeypatch.setattr(config, "AI_MODEL", "gemini-old")

    def fake_post(url, headers, body):
        raise _http_error(404, "gemini-old is no longer available, use gemini-3.8-flash")
    monkeypatch.setattr(ai, "_post_stream", fake_post)
    with pytest.raises(ai.AIError, match="no longer available"):
        list(ai.stream([{"role": "user", "content": "q"}]))


@pytest.mark.parametrize("status,expected", [(429, "free-tier limit"), (403, "API key was rejected"),
                                             (500, "boom")])
def test_stream_failures_read_as_words(gemini, monkeypatch, status, expected):
    def fake_post(url, headers, body):
        raise _http_error(status, "boom")
    monkeypatch.setattr(ai, "_post_stream", fake_post)
    with pytest.raises(ai.AIError, match=expected):
        list(ai.stream([{"role": "user", "content": "q"}]))


# --------------------------------------------------------------------------- #
# The endpoint and the summary
# --------------------------------------------------------------------------- #
def _capture(monkeypatch, answer=("Block 7 ", "looks fine.")):
    """Stands in for the provider; records what it was sent and whether
    Boord's database was open while it was asked."""
    seen = {}
    counter = _OpenConnectionCounter(boord_engine).__enter__()

    def fake_post(url, headers, body):
        seen["body"] = body
        seen["boord_open"] = counter.live
        return iter(_gemini_sse(*answer))
    monkeypatch.setattr(ai, "_post_stream", fake_post)
    return seen, counter


def _summary_sent(seen):
    first_user = seen["body"]["contents"][0]["parts"][0]["text"]
    return json.loads(first_user.split("figures:\n\n", 1)[1].split("\n\nQuestion:", 1)[0])


def test_ask_streams_an_answer_from_the_saved_estimate(client, gemini, monkeypatch):
    _seed_history()
    est = client.post("/api/estimate", json={"season_year": 2026, "name": "January"}).json()
    client.put(f"/api/estimate/{est['id']}", json={
        "notes": "Good flowering on 7",
        "lines": [{"block_id": "7", "trees": 2000, "kg_per_tree": 150.0, "note": "heavy set"},
                  {"block_id": "8a", "trees": 500, "kg_per_tree": None}],
        "pack": [{"channel": "Export", "pack_type": "4.5 kg", "kg_per_carton": 4.8, "share_pct": 60},
                 {"channel": "Juice", "share_pct": 30}]})
    seen, counter = _capture(monkeypatch)
    try:
        r = client.post("/api/ai/ask", json={"season": 2026, "question": "Review this estimate"})
    finally:
        counter.__exit__()
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    events = _ndjson(r)
    assert "".join(e.get("t", "") for e in events) == "Block 7 looks fine."
    done = events[-1]
    assert done["done"] and done["model"] == "gemini-3.8-flash"
    assert "7" in done["blocks"] and "90" not in done["blocks"]
    assert 2025 in done["years"] and 2026 in done["years"]
    # Boord's database was closed before the provider was called.
    assert seen["boord_open"] == 0

    s = _summary_sent(seen)
    assert s["context"]["season_year"] == 2026 and s["context"]["unsaved_changes_included"] is False
    b7 = next(b for b in s["blocks"] if b["block"] == "7")
    assert b7["estimate_kg"] == 300_000 and b7["high_kg_tree"] == 100.0 and b7["note"] == "heavy set"
    assert b7["history_kg_tree"]["2025"] == 30.0 and 2019 in b7["annual_only_years"]
    assert s["estimate"]["notes"] == "Good flowering on 7" and s["estimate"]["blocks_estimated"] == 1
    assert s["pack_out"]["allocated_pct"] == 90.0
    hl = " ".join(s["highlights"])
    assert "No kg/tree estimate yet for block(s) 8a" in hl
    assert "above its 10-season high of 100.0" in hl
    assert "add up to 90.0%" in hl
    # Another grower's block is never sent.
    assert all(b["block"] != "90" for b in s["blocks"])
    # The rules travel as the system instruction.
    assert "Use ONLY the figures" in seen["body"]["systemInstruction"]["parts"][0]["text"]


def test_ask_reviews_unsaved_edits_and_the_live_weather_model(client, gemini, monkeypatch):
    _seed_history()
    est = client.post("/api/estimate", json={"season_year": 2026, "name": "January"}).json()
    seen, counter = _capture(monkeypatch)
    try:
        r = client.post("/api/ai/ask", json={
            "season": 2026, "estimate_id": est["id"], "question": "How does it compare with the weather model?",
            "draft": {"name": "Draft", "notes": "", "pack": [],
                      "lines": [{"block_id": "7", "trees": 2000, "kg_per_tree": 40.0},
                                {"block_id": "8a", "trees": 500, "kg_per_tree": 20.0}]},
            "forecast": {"season_year": 2026, "built_at": datetime.now(timezone.utc).isoformat(),
                         "favorable_kg": 110_000, "expected_kg": 80_000, "unfavorable_kg": 60_000,
                         "live": True, "settled": 1},
        })
    finally:
        counter.__exit__()
    assert r.status_code == 200
    s = _summary_sent(seen)
    assert s["context"]["unsaved_changes_included"] is True and s["context"]["estimate_name"] == "Draft"
    assert s["estimate"]["total_kg"] == 90_000
    w = s["weather_model"]
    assert w["source"] == "live" and w["position"] == "within" and w["gap_pct"] == 12.5
    assert any("within the weather model's range" in h for h in s["highlights"])


def test_follow_ups_carry_the_figures_once(client, gemini, monkeypatch):
    seen, counter = _capture(monkeypatch)
    try:
        r = client.post("/api/ai/ask", json={
            "season": 2026, "question": "Why?",
            "history": [{"q": f"q{i}", "a": f"a{i}"} for i in range(5)]})
    finally:
        counter.__exit__()
    assert r.status_code == 200
    contents = seen["body"]["contents"]
    # The last three earlier turns, then the new question.
    assert [c["parts"][0]["text"] for c in contents][1:] == ["a2", "Question: q3", "a3", "Question: q4", "a4", "Question: Why?"]
    assert contents[0]["parts"][0]["text"].startswith("Here are the Estimate tab's figures")
    assert "Question: q2" in contents[0]["parts"][0]["text"]
    assert _summary_sent(seen)["context"]["has_estimate"] is False


def test_a_provider_refusal_reaches_the_browser_as_words(client, gemini, monkeypatch):
    def fake_post(url, headers, body):
        raise _http_error(429, "Resource exhausted")
    monkeypatch.setattr(ai, "_post_stream", fake_post)
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "Review this estimate"})
    assert r.status_code == 200
    assert _ndjson(r) == [{"error": "Google Gemini: free-tier limit reached, try again shortly"}]


def test_ask_validates_like_a_save(client, gemini):
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "x", "draft": {
        "lines": [{"block_id": "7", "trees": -1}]}})
    assert r.status_code == 422
    assert client.post("/api/ai/ask", json={"season": 2026, "question": ""}).status_code == 422


# --------------------------------------------------------------------------- #
# Ask AI about this weather (the Weather tab)
# --------------------------------------------------------------------------- #
def _seed_weather(years=(2023, 2024, 2025, 2026)):
    """Noon and midnight on 1 and 2 July of each year (rain on the 2nd), at
    the farm_has_gps fixture's coordinates."""
    from datetime import datetime as dt
    from db import owner_engine
    from models_owner import WeatherHistory
    from sqlmodel import Session
    with Session(owner_engine) as s:
        for y in years:
            for day, rain in ((1, 0.0), (2, 4.0)):
                for hour, t in ((0, 2.0 + y % 10), (12, 14.0 + y % 10)):
                    s.add(WeatherHistory(timestamp=dt(y, 7, day, hour), temp_c=t, precipitation_mm=rain,
                                         lat=-34.0, lon=18.5))
        s.commit()


def _clear_weather():
    from db import owner_engine
    from models_owner import WeatherHistory
    from sqlmodel import Session, delete
    import weather as weather_module
    with Session(owner_engine) as s:
        s.exec(delete(WeatherHistory))
        s.commit()
    weather_module.invalidate_history_stats()
    weather_module._cache.clear()


def _forecast_payload(today, low=1.0):
    times = [f"{today.isoformat()}T{h:02d}:00" for h in range(24)]
    return {"hourly": {"time": times, "temperature_2m": [low if h < 6 else 20.0 for h in range(24)],
                       "precipitation": [0.5] * 24, "wind_speed_10m": [10.0] * 24,
                       "relative_humidity_2m": [60.0] * 24, "weather_code": [0] * 24}}


@pytest.fixture()
def weather_record(farm_has_gps):
    from routers import ai_weather
    _clear_weather()
    ai_weather._forecast_cache.clear()
    _seed_weather()
    yield
    _clear_weather()
    ai_weather._forecast_cache.clear()


def _ask_weather(client, monkeypatch, **body):
    seen, counter = _capture(monkeypatch, answer=("2025 was ", "wetter."))
    try:
        r = client.post("/api/ai/ask", json={"tab": "weather", "question": "Which year was warmest?", **body})
    finally:
        counter.__exit__()
    return r, seen


def test_weather_ask_sends_the_record_and_the_forecast(client, gemini, weather_record, monkeypatch):
    from datetime import date
    from routers import ai_weather
    monkeypatch.setattr(ai_weather, "fetch_forecast_hourly",
                        lambda lat, lon, days=8, timeout=15: _forecast_payload(date.today()))
    monkeypatch.setattr(ai_weather, "fetch_weather_cached", lambda lat, lon: {"temp": 11.0, "humidity": 70})
    r, seen = _ask_weather(client, monkeypatch, years=[2024, 2025, 1900], metrics=["temp_max_c", "precipitation_mm", "nope"])
    assert r.status_code == 200
    events = _ndjson(r)
    assert "".join(e.get("t", "") for e in events) == "2025 was wetter."
    done = events[-1]
    assert done["done"] and 2025 in done["years"] and 1900 not in done["years"]
    # The coordinates were read and Boord let go before the forecast went out.
    assert seen["boord_open"] == 0

    s = _summary_sent(seen)
    assert [m["key"] for m in s["measurements"]] == ["temp_max_c", "precipitation_mm"]
    assert sorted(s["selected_years"]) == ["2024", "2025"]
    rain = s["selected_years"]["2025"]["precipitation_mm"]
    assert rain["days"] == 2 and rain["total"] == 8.0 and rain["days_with_1mm_or_more"] == 1
    assert s["selected_years"]["2025"]["temp_max_c"]["highest_day"]["value"] == 19.0
    # Every year on file ranks against the others; coordinates are never sent.
    from datetime import date as _d
    assert sorted(s["record"]["temp_max_c"]["by_year"]) == [str(y) for y in (2023, 2024, 2025) if y < _d.today().year]
    assert s["record"]["temp_max_c"]["by_year"]["2025"] == 19.0
    assert "-34.0" not in json.dumps(s)
    assert s["forecast"]["frost_nights_at_or_below_c"]["dates"] == [date.today().isoformat()]
    assert s["current"] == {"temp": 11.0, "humidity": 70}
    assert any("Forecast nights at or below" in h for h in s["highlights"])


def test_weather_ask_survives_an_unreachable_forecast(client, gemini, weather_record, monkeypatch):
    from routers import ai_weather

    def boom(*a, **k):
        raise OSError("no route")
    monkeypatch.setattr(ai_weather, "fetch_forecast_hourly", boom)
    monkeypatch.setattr(ai_weather, "fetch_weather_cached", lambda lat, lon: {})
    r, seen = _ask_weather(client, monkeypatch)
    assert r.status_code == 200
    s = _summary_sent(seen)
    assert s["forecast"] is None and s["current"] is None
    # No years or measurements ticked: the latest year and the mean temperature.
    assert list(s["selected_years"]) == ["2026"] and s["measurements"][0]["key"] == "temp_c"


def test_weather_ask_without_a_location_has_no_forecast(client, gemini, monkeypatch):
    _clear_weather()
    r, seen = _ask_weather(client, monkeypatch)
    assert r.status_code == 200
    s = _summary_sent(seen)
    assert s["forecast"] is None and "no forecast" in s["context"]["location"]
