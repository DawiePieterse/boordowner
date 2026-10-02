"""The Anthropic provider (ai.py), what Ask can look up (ai_tools.py), and
the endpoints built on them: Check, Compare, the daily brief and the
Boord Notes bridge. No test reaches a real provider or Notes: the SDK
client is stood in for at ai._anthropic_client, and the Notes call at
ai_tools._post_json."""
import json
import urllib.error
from datetime import date
from io import BytesIO
from types import SimpleNamespace

import pytest

import ai
import ai_tools
import config
from models_owner import SeasonBrief
from routers import ai as ai_router
from tests.test_ai import _gemini_sse, _http_error, _ndjson, gemini  # noqa: F401 - the fixture
from tests.test_estimate import _seed_history


# --------------------------------------------------------------------------- #
# A stand-in for the SDK client
# --------------------------------------------------------------------------- #
class _Block(SimpleNamespace):
    pass


def text_block(text):
    return _Block(type="text", text=text)


def tool_block(name, inp, id_="toolu_1"):
    return _Block(type="tool_use", name=name, input=inp, id=id_)


def message(content, stop_reason="end_turn", cached=0):
    return SimpleNamespace(content=content, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                                 cache_read_input_tokens=cached, cache_creation_input_tokens=0))


class FakeStream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        for b in self.final.content:
            if b.type == "text":
                yield from (b.text[i:i + 6] for i in range(0, len(b.text), 6))

    def get_final_message(self):
        return self.final


class FakeMessages:
    """`replies` is consumed one per request, streamed or not; every
    request's kwargs are kept in `calls`."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeStream(r)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)
        self.options = []

    def with_options(self, **kw):
        self.options.append(kw)
        return self


@pytest.fixture()
def claude(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "AI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AI_MODEL", "")
    monkeypatch.setattr(config, "AI_DAILY_LIMIT", 200)
    monkeypatch.setattr(config, "NOTES_URL", "")
    ai._usage.update(day=None, count=0)

    def install(*replies):
        fake = FakeClient(replies)
        monkeypatch.setattr(ai, "_anthropic_client", lambda s: fake)
        return fake
    yield install
    ai._usage.update(day=None, count=0)


# --------------------------------------------------------------------------- #
# Settings and the cap
# --------------------------------------------------------------------------- #
def test_anthropic_key_falls_back_to_env_and_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AI_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(config, "AI_KEY_FILE", str(tmp_path / "missing.txt"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai.settings() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    assert ai.settings()["api_key"] == "sk-env" and ai.has_tools(ai.settings())
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    keyfile = tmp_path / "anthropic_key.txt"
    keyfile.write_text("sk-file\n")
    monkeypatch.setattr(config, "AI_KEY_FILE", str(keyfile))
    s = ai.settings()
    assert s["api_key"] == "sk-file" and ai.model_for(s) == "claude-sonnet-5-5"
    assert ai.provider_name(s) == "Anthropic Claude"


def test_status_reports_tools_and_the_cap(client, claude):
    claude()
    st = client.get("/api/ai/status").json()
    assert st["configured"] and st["tools"] is True and st["notes"] is False
    assert st["calls_today"] == 0 and st["daily_limit"] == 200


def test_daily_cap_stops_the_spend(client, claude, monkeypatch):
    fake = claude(message([text_block("ok")]), message([text_block("ok")]))
    monkeypatch.setattr(config, "AI_DAILY_LIMIT", 1)
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "Review"})
    assert _ndjson(r)[-1]["done"]
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "Review"})
    assert _ndjson(r) == [{"error": "The daily limit for AI help has been reached. It resets tomorrow."}]
    assert len(fake.messages.calls) == 1
    assert client.get("/api/ai/status").json()["calls_today"] == 1


# --------------------------------------------------------------------------- #
# The streamed answer
# --------------------------------------------------------------------------- #
def test_ask_streams_through_the_sdk_with_caching(client, claude):
    fake = claude(message([text_block("Block 7 looks fine.")], cached=5000))
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "Review this estimate"})
    events = _ndjson(r)
    assert "".join(e.get("t", "") for e in events) == "Block 7 looks fine."
    assert events[-1]["done"] and events[-1]["model"] == "claude-sonnet-5-5"
    kw = fake.messages.calls[0]
    assert kw["model"] == "claude-sonnet-5-5" and kw["output_config"] == {"effort": "low"}
    # The system prompt and the figures carry cache breakpoints; the
    # question rides in the same first turn as the figures.
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Use ONLY the figures" in kw["system"][0]["text"]
    assert "You can look things up" in kw["system"][0]["text"]
    first = kw["messages"][0]
    assert first["role"] == "user" and first["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Question: Review this estimate" in first["content"][0]["text"]
    assert {t["name"] for t in kw["tools"]} == {"block_history", "season_weather", "picking_pace"}


def test_follow_ups_keep_the_first_turn_byte_identical(client, claude):
    """What makes the cache hit: the figures' turn is rebuilt the same."""
    fake = claude(message([text_block("a")]), message([text_block("b")]))
    client.post("/api/ai/ask", json={"season": 2026, "question": "q1"})
    client.post("/api/ai/ask", json={"season": 2026, "question": "Why?", "history": [{"q": "q1", "a": "a"}]})
    m1, m2 = fake.messages.calls[0]["messages"], fake.messages.calls[1]["messages"]
    assert m1[0] == m2[0]
    assert [m["role"] for m in m2] == ["user", "assistant", "user"]
    assert m2[-1]["content"] == "Question: Why?"


def test_ask_runs_a_lookup_the_model_asks_for(client, claude):
    _seed_history()
    fake = claude(
        message([text_block("Let me check. "), tool_block("block_history", {"block_id": "7"})], stop_reason="tool_use"),
        message([text_block("Block 7's best season was 2016 at 100 kg/tree.")]),
    )
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "What was block 7's best season?"})
    events = _ndjson(r)
    assert {"step": "Looking up block 7's history..."} in events
    assert "".join(e.get("t", "") for e in events) == "Let me check. Block 7's best season was 2016 at 100 kg/tree."
    # The second request carries the assistant turn as-is and the result.
    msgs = fake.messages.calls[1]["messages"]
    assert msgs[1]["role"] == "assistant" and msgs[2]["role"] == "user"
    result = msgs[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    data = json.loads(result["content"])
    assert data["block"] == "7" and {s["season"]: s["kg_tree"] for s in data["seasons"]}[2016] == 100.0


def test_a_failed_lookup_is_told_to_the_model(client, claude):
    fake = claude(
        message([tool_block("season_weather", {"year": 1500})], stop_reason="tool_use"),
        message([text_block("No weather on file for 1500.")]),
    )
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "How was 1500's weather?"})
    assert _ndjson(r)[-1]["done"]
    result = fake.messages.calls[1]["messages"][2]["content"][0]
    assert "No weather on file" in result["content"]


def test_too_many_lookups_ends_in_words(client, claude, monkeypatch):
    monkeypatch.setattr(ai, "MAX_TOOL_ROUNDS", 1)
    claude(*[message([tool_block("picking_pace", {"year": 2025})], stop_reason="tool_use")] * 3)
    r = client.post("/api/ai/ask", json={"season": 2026, "question": "pace?"})
    assert "too many lookups" in _ndjson(r)[-1]["error"]


@pytest.mark.parametrize("stop,expected", [("refusal", "declined"), ("max_tokens", "cut short")])
def test_stop_reasons_reach_the_browser(client, claude, stop, expected):
    claude(message([text_block("Half an ans")], stop_reason=stop))
    events = _ndjson(client.post("/api/ai/ask", json={"season": 2026, "question": "Review"}))
    assert "".join(e.get("t", "") for e in events) == "Half an ans" and expected in events[-1]["error"]


def test_sdk_errors_read_as_words(client, claude):
    import anthropic
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(401, request=req)
    claude(anthropic.AuthenticationError("bad key", response=resp, body=None))
    assert _ndjson(client.post("/api/ai/ask", json={"season": 2026, "question": "Review"})) == \
        [{"error": "Anthropic: the API key was rejected"}]


# --------------------------------------------------------------------------- #
# Finish reasons on the free-tier providers
# --------------------------------------------------------------------------- #
def test_gemini_truncation_is_reported_after_the_text(client, gemini, monkeypatch):
    lines = _gemini_sse("Block 7 ", "looks")
    lines.append(f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': ' fi'}]}, 'finishReason': 'MAX_TOKENS'}]})}\n".encode())
    monkeypatch.setattr(ai, "_post_stream", lambda url, headers, body: iter(lines))
    events = _ndjson(client.post("/api/ai/ask", json={"season": 2026, "question": "Review"}))
    assert "".join(e.get("t", "") for e in events) == "Block 7 looks fi"
    assert "cut short" in events[-1]["error"]


def test_finish_reason_helpers():
    g = {"provider": "gemini"}
    assert ai.finish_reason(g, {"candidates": [{"finishReason": "STOP"}]}) == "STOP"
    assert ai.finish_reason({"provider": "groq"}, {"choices": [{"finish_reason": "length"}]}) == "length"
    assert ai.finish_reason(g, {"candidates": [{"content": {}}]}) is None
    with pytest.raises(ai.AIError, match="declined"):
        ai._check_finish("SAFETY", "X")
    ai._check_finish("STOP", "X")


# --------------------------------------------------------------------------- #
# Check: the structured review
# --------------------------------------------------------------------------- #
def _estimate(client):
    _seed_history()
    est = client.post("/api/estimate", json={"season_year": 2026, "name": "January"}).json()
    client.put(f"/api/estimate/{est['id']}", json={
        "notes": "Good flowering on 7",
        "lines": [{"block_id": "7", "trees": 2000, "kg_per_tree": 150.0, "note": "heavy set"},
                  {"block_id": "8a", "trees": 500, "kg_per_tree": None}],
        "pack": []})
    return est


REVIEW = {"overall": "Block 7 sits above its record.",
          "findings": [
              {"block": "7", "severity": "high", "finding": "150 is above the 10-season high of 100.",
               "suggested_low_kg_tree": 80, "suggested_high_kg_tree": 45},
              {"block": "8A", "severity": "medium", "finding": "No estimate yet.",
               "suggested_low_kg_tree": None, "suggested_high_kg_tree": None},
              {"block": "99", "severity": "ok", "finding": "made up", "suggested_low_kg_tree": -3,
               "suggested_high_kg_tree": None},
              {"block": "7", "severity": "low", "finding": "duplicate", "suggested_low_kg_tree": None,
               "suggested_high_kg_tree": None},
          ],
          "farm_findings": ["Total is above last season.", ""]}


def test_review_is_verified_block_by_block(client, claude):
    _estimate(client)
    fake = claude(message([text_block(json.dumps(REVIEW))]))
    r = client.post("/api/ai/review", json={"season": 2026})
    assert r.status_code == 200, r.text
    body = r.json()
    kw = fake.messages.calls[0]
    assert kw["output_config"]["format"]["type"] == "json_schema" and kw["output_config"]["effort"] == "medium"
    assert fake.options == [{"max_retries": 1}]
    assert "structured check" in kw["system"][0]["text"]
    assert body["overall"] == REVIEW["overall"] and body["farm_findings"] == ["Total is above last season."]
    assert [f["block"] for f in body["findings"]] == ["7", "8a"]   # snapped spelling, one per block
    assert body["findings"][0]["suggested_low_kg_tree"] == 45.0 and body["findings"][0]["suggested_high_kg_tree"] == 80.0
    assert body["dropped"] == ["99", "7"]
    assert body["unsaved_changes_included"] is False and body["model"] == "claude-sonnet-5-5"


def test_review_needs_an_estimate_and_counts_against_the_cap(client, claude):
    claude()
    assert client.post("/api/ai/review", json={"season": 2026}).status_code == 400
    _estimate(client)
    claude(message([text_block("{}")], stop_reason="refusal"))
    r = client.post("/api/ai/review", json={"season": 2026})
    assert r.status_code == 503 and "declined" in r.json()["detail"]


def test_review_on_a_free_tier_provider_uses_json_mode(client, gemini, monkeypatch):
    _estimate(client)
    seen = {}

    def fake_post(url, headers, body):
        seen["url"], seen["body"] = url, body
        reply = {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(REVIEW) + "\n```"}]},
                                 "finishReason": "STOP"}]}
        return iter([json.dumps(reply).encode()])
    monkeypatch.setattr(ai, "_post_stream", fake_post)
    r = client.post("/api/ai/review", json={"season": 2026})
    assert r.status_code == 200, r.text
    assert seen["url"].endswith(":generateContent")
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "matching this schema exactly" in seen["body"]["contents"][0]["parts"][0]["text"]
    assert [f["block"] for f in r.json()["findings"]] == ["7", "8a"]


# --------------------------------------------------------------------------- #
# Compare
# --------------------------------------------------------------------------- #
def test_compare_sends_both_versions_with_the_deltas(client, claude):
    first = _estimate(client)
    second = client.post("/api/estimate", json={"season_year": 2026, "name": "February",
                                                "copy_from_id": first["id"]}).json()
    client.put(f"/api/estimate/{second['id']}", json={
        "lines": [{"block_id": "7", "trees": 2000, "kg_per_tree": 120.0, "note": "set thinner than it looked"},
                  {"block_id": "8a", "trees": 500, "kg_per_tree": 20.0}]})
    fake = claude(message([text_block("Block 7 came down 20%.")]))
    r = client.post("/api/ai/compare", json={"from_id": second["id"], "to_id": first["id"]})   # order fixed server-side
    events = _ndjson(r)
    assert events[-1]["done"] and events[-1]["blocks"] == ["7", "8a"]
    kw = fake.messages.calls[0]
    assert "two versions" in kw["system"][0]["text"]
    sent = json.loads(kw["messages"][0]["content"][0]["text"].split("versions:\n\n", 1)[1].split("\n\nQuestion:", 1)[0])
    assert sent["from"]["name"] == "January" and sent["to"]["name"] == "February"
    b7 = next(b for b in sent["blocks"] if b["block"] == "7")
    assert b7["from_kg_tree"] == 150.0 and b7["to_kg_tree"] == 120.0 and b7["change_kg"] == -60_000
    assert b7["change_pct"] == -20.0 and b7["to_note"] == "set thinner than it looked"
    assert sent["total_change_kg"] == -50_000
    assert client.post("/api/ai/compare", json={"from_id": 1, "to_id": 999}).status_code == 404


# --------------------------------------------------------------------------- #
# The daily brief
# --------------------------------------------------------------------------- #
def test_brief_is_written_once_a_day_and_served_from_the_db(client, claude, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    assert client.get("/api/ai/brief").json() == {"brief": None, "configured": False}
    monkeypatch.setattr(config, "AI_API_KEY", "sk-test")
    claude()
    # Nothing to write against yet.
    assert client.post("/api/ai/brief/refresh").status_code == 503
    ai_router.write_brief_if_due()   # quiet, nothing stored
    assert client.get("/api/ai/brief").json()["brief"] is None

    _estimate(client)
    fake = claude(message([text_block("Picking is on pace. Block 7 worth a look.")]))
    r = client.post("/api/ai/brief/refresh")
    assert r.status_code == 200, r.text
    b = r.json()["brief"]
    assert b["text"].startswith("Picking is on pace") and b["today"] and b["season_year"] == 2026
    kw = fake.messages.calls[0]
    assert "morning brief" in kw["system"][0]["text"]
    assert "weather_model_expected_kg_last_days" in kw["messages"][0]["content"][0]["text"]
    # The background job sees today's row and does not spend again.
    ai_router.write_brief_if_due()
    assert len(fake.messages.calls) == 1
    got = client.get("/api/ai/brief").json()
    assert got["brief"]["text"] == b["text"] and got["configured"]


def test_brief_job_writes_when_missing(client, claude, monkeypatch):
    _estimate(client)
    fake = claude(message([text_block("Brief.")]))
    ai_router.write_brief_if_due()
    assert len(fake.messages.calls) == 1
    from sqlmodel import Session, select
    from db import owner_engine
    with Session(owner_engine) as s:
        rows = s.exec(select(SeasonBrief)).all()
    assert len(rows) == 1 and rows[0].brief_date == date.today() and rows[0].provider == "Anthropic Claude"


# --------------------------------------------------------------------------- #
# Boord Notes
# --------------------------------------------------------------------------- #
def test_notes_bridge_off_without_a_url(client):
    assert client.post("/api/ai/notes", json={"question": "Spray?"}).status_code == 503


def test_notes_bridge_streams_the_answer_and_sources(client, monkeypatch, claude):
    claude()
    monkeypatch.setattr(config, "NOTES_URL", "http://127.0.0.1:8020")
    monkeypatch.setattr(config, "NOTES_PUBLIC_URL", "https://farm.tailnet.ts.net:9443")
    seen = {}

    def fake_post(url, headers, body, timeout=None):
        seen["url"], seen["body"], seen["timeout"] = url, body, timeout
        return iter([json.dumps({"answer": "Spray in week 2 of October.", "notes_considered": 3, "notes_total": 40,
                                 "sources": [{"id": "x", "title": "Spuitprogram", "created_at": "2024-10-02T08:00:00"}]}).encode()])
    monkeypatch.setattr(ai, "_post_stream", fake_post)
    assert client.get("/api/ai/status").json()["notes"] is True
    events = _ndjson(client.post("/api/ai/notes", json={"question": "When do we spray block 4?"}))
    assert seen["url"] == "http://127.0.0.1:8020/api/ai/ask" and seen["body"] == {"question": "When do we spray block 4?"}
    assert seen["timeout"] == ai_tools.NOTES_TIMEOUT_S
    assert events[0] == {"t": "Spray in week 2 of October."}
    done = events[-1]
    assert done["provider"] == "Boord Notes" and done["notes_total"] == 40
    assert done["sources"] == [{"title": "Spuitprogram", "date": "2024-10-02", "url": "https://farm.tailnet.ts.net:9443/app/"}]


def test_notes_bridge_passes_notes_own_sentence_through(client, monkeypatch, claude):
    claude()
    monkeypatch.setattr(config, "NOTES_URL", "http://127.0.0.1:8020")

    def fake_post(url, headers, body, timeout=None):
        raise urllib.error.HTTPError(url, 503, "x", {}, BytesIO(json.dumps({"detail": "AI help is not set up on the server yet."}).encode()))
    monkeypatch.setattr(ai, "_post_stream", fake_post)
    events = _ndjson(client.post("/api/ai/notes", json={"question": "q"}))
    assert events == [{"error": "Boord Notes: AI help is not set up on the server yet."}]


def test_notes_become_a_tool_for_claude(client, monkeypatch, claude):
    monkeypatch.setattr(config, "NOTES_URL", "http://127.0.0.1:8020")
    monkeypatch.setattr(ai, "_post_stream", lambda url, headers, body, timeout=None: iter([json.dumps({
        "answer": "Andre noted hail on 8a in 2023.", "sources": [{"title": "Hael", "created_at": "2023-11-01"}]}).encode()]))
    fake = claude(
        message([tool_block("farm_notes", {"question": "What was noted about 8a?"})], stop_reason="tool_use"),
        message([text_block("The notes mention hail on 8a in 2023 (Hael, 2023-11-01).")]),
    )
    events = _ndjson(client.post("/api/ai/ask", json={"season": 2026, "question": "Anything noted about 8a?"}))
    assert {"step": "Asking the farm notes..."} in events and events[-1]["done"]
    assert "farm_notes" in {t["name"] for t in fake.messages.calls[0]["tools"]}
    assert "notebook" in fake.messages.calls[0]["system"][0]["text"]
    assert "hail on 8a" in fake.messages.calls[1]["messages"][2]["content"][0]["content"]


# --------------------------------------------------------------------------- #
# The lookups themselves
# --------------------------------------------------------------------------- #
def test_lookups_answer_from_the_record(client):
    _seed_history()
    h = ai_tools.run_tool("block_history", {"block_id": "7"})
    by = {s["season"]: s for s in h["seasons"]}
    assert h["trees_now"] == 2000 and by[2025]["kg"] == 60_000 and by[2025]["kg_tree"] == 30.0
    assert by[2019]["annual_total_only"] and not by[2025]["annual_total_only"]
    assert by[2025]["first_pick"] == "2025-09-01" and by[2025]["span_days"] == 62
    assert "error" in ai_tools.run_tool("block_history", {"block_id": "nope"})
    assert "error" in ai_tools.run_tool("block_history", {"block_id": "90"})   # the neighbour's

    p = ai_tools.run_tool("picking_pace", {"year": 2025, "block_id": "7"})
    assert p["total_kg"] == 60_000 and p["picking_days"] == 2 and len(p["kg_per_week"]) == 2
    assert ai_tools.run_tool("picking_pace", {"year": 2025})["block"] == "whole farm"
    assert "error" in ai_tools.run_tool("picking_pace", {"year": 1999})
    assert "error" in ai_tools.run_tool("season_weather", {"year": 2025})   # no weather seeded
    with pytest.raises(ValueError):
        ai_tools.run_tool("nope", {})


def test_lookups_release_boord_before_returning(client):
    from tests.test_boord_isolation import _OpenConnectionCounter
    from db import boord_engine
    _seed_history()
    with _OpenConnectionCounter(boord_engine) as counter:
        ai_tools.run_tool("block_history", {"block_id": "7"})
        assert counter.live == 0
