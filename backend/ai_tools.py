"""What Ask may look up for itself, when the provider can ask for it
(Claude's tool use - see ai.stream_events).

The Estimate tab's summary carries the figures the tab shows; a deeper
question ("compare block 7's 2022 with now", "how did 2019's spring
weather differ", "what did Andre write about 8a") needs figures the summary
would be ten times the size to carry on every call. So the model is told
what it can fetch and fetches only that.

Every tool here opens its own sessions and closes them before returning:
the provider call is in flight around these, and Boord's database must
never be held open across it (db.get_boord_session). Each returns plain
JSON-ready data; the model does the reading.

farm_notes is the bridge to Boord Notes (config.NOTES_URL): one app asking
the other its own Ask question over localhost. The apps still share no
database - Notes answers from its notes, and its answer plus the notes it
used come back here as data.
"""
import json
import urllib.error
import urllib.request
from datetime import date
from typing import Optional

from sqlmodel import Session, select

import config
from db import boord_engine, owner_engine
from models_owner import WeatherHistory
from routers.analogs import _season_timing, _weather_values
from routers.analysis import block_season_kg, season_day_kg
from routers.risk import DRIVERS

NOTES_TIMEOUT_S = 110   # Notes' own Ask reads every note: slow, not stuck

TOOLS = [
    {
        "name": "block_history",
        "description": ("One block's season-by-season record: kg and kg/tree (on the block's current "
                        "tree count) for every season on file, which seasons were recorded as totals "
                        "only, and for daily-tracked seasons when picking started and how long it ran. "
                        "Use it when a question needs more of a block's past than the summary's "
                        "last-5 and 10-season figures."),
        "input_schema": {"type": "object", "properties": {
            "block_id": {"type": "string", "description": "The block as the summary names it, e.g. \"8a\""}},
            "required": ["block_id"], "additionalProperties": False},
        "step": lambda i: f"Looking up block {i.get('block_id', '?')}'s history...",
    },
    {
        "name": "season_weather",
        "description": ("One season's weather on the farm: the four factors the weather model uses "
                        "(each over its fixed calendar window) with the farm's average of each across "
                        "all seasons on file, plus a month-by-month summary (mean and max temperature, "
                        "rain, sunshine hours) for the season's months. Use it to compare seasons' "
                        "weather or explain the similar-seasons match."),
        "input_schema": {"type": "object", "properties": {
            "year": {"type": "integer", "description": "The season year, e.g. 2022"}},
            "required": ["year"], "additionalProperties": False},
        "step": lambda i: f"Looking up {i.get('year', '?')}'s weather...",
    },
    {
        "name": "picking_pace",
        "description": ("How a season's picking ran: first and last picking day, the day it got going, "
                        "total kg, and kg per week through the season - for the whole farm or one block. "
                        "Daily-tracked seasons only. Use it for pace, timing and \"are we on track\" "
                        "questions that need more than the summary's progress figures."),
        "input_schema": {"type": "object", "properties": {
            "year": {"type": "integer", "description": "The season year"},
            "block_id": {"type": "string", "description": "Optional: one block; leave out for the whole farm"}},
            "required": ["year"], "additionalProperties": False},
        "step": lambda i: f"Looking up {i.get('year', '?')}'s picking{(' on block ' + str(i['block_id'])) if i.get('block_id') else ''}...",
    },
]

NOTES_TOOL = {
    "name": "farm_notes",
    "description": ("Asks Boord Notes, the farm's own notebook, a question in plain words - Andre's "
                    "notes over the years on blocks, procedures, pests, timing. Returns its answer "
                    "(written only from the notes) and the notes it relied on. Use it when the "
                    "question is about what was observed or done in the orchard rather than a figure "
                    "in the summary, and say which notes the answer came from."),
    "input_schema": {"type": "object", "properties": {
        "question": {"type": "string", "description": "The question for the notes, in English or Afrikaans"}},
        "required": ["question"], "additionalProperties": False},
    "step": lambda i: "Asking the farm notes...",
}


def available_tools() -> list:
    """The tools for one Ask: the three lookups, plus the notes when Boord
    Notes is reachable by configuration."""
    return TOOLS + ([NOTES_TOOL] if config.NOTES_URL else [])


def _r(x, nd=1):
    return round(x, nd) if x is not None else None


# --------------------------------------------------------------------------- #
# The lookups
# --------------------------------------------------------------------------- #
def block_history(block_id: str) -> dict:
    block_id = (block_id or "").strip()
    with Session(boord_engine) as boord, Session(owner_engine) as owner:
        s = season_day_kg(boord, owner)
        block_year_kg, annual_only = block_season_kg(owner, s)
    # Both sessions are closed here; everything below is arithmetic.
    b = s["blocks"].get(block_id)
    years = block_year_kg.get(block_id) or {}
    if b is None and not years:
        return {"error": f"No block {block_id!r} on this farm. Blocks on file: "
                         + ", ".join(sorted(s["blocks"]))}
    trees = (b.trees or 0) if b else 0
    seasons = []
    for y in sorted(years):
        kg = years[y]
        row = {"season": y, "kg": _r(kg, 0), "kg_tree": _r(kg / trees, 1) if trees else None,
               "annual_total_only": y in annual_only,
               "in_progress": y == s["current_year"]}
        if y not in annual_only:
            timing = _season_timing({k: v for k, v in s["day_kg"].items() if k[1] == block_id}, y)
            if timing:
                row.update({"first_pick": timing["first_date"], "got_going": timing["start_date"],
                            "last_pick": timing["last_date"], "span_days": timing["span_days"]})
        seasons.append(row)
    return {"block": block_id, "variety": b.variety if b else None, "trees_now": trees,
            "active": bool(b.active) if b else False,
            "note": "kg/tree uses the block's current tree count for every season",
            "seasons": seasons}


def _month_summary(owner: Session, year: int) -> list:
    """Mean/max temperature, rain and sunshine per month, over the season's
    months (the weather model's windows run Aug-Nov; picking runs after)."""
    start, end = date(year, 7, 1), date(year + 1, 1, 1)
    rows = owner.exec(select(WeatherHistory.timestamp, WeatherHistory.temp_c, WeatherHistory.precipitation_mm,
                             WeatherHistory.sunshine_duration_s)
                      .where(WeatherHistory.timestamp >= start, WeatherHistory.timestamp < end)).all()
    by_month: dict = {}
    for r in rows:
        m = by_month.setdefault(r.timestamp.month, {"temps": [], "rain": 0.0, "sun_s": 0.0, "hours": 0})
        m["hours"] += 1
        if r.temp_c is not None:
            m["temps"].append(r.temp_c)
        if r.precipitation_mm:
            m["rain"] += r.precipitation_mm
        if r.sunshine_duration_s:
            m["sun_s"] += r.sunshine_duration_s
    out = []
    for month in sorted(by_month):
        m = by_month[month]
        out.append({"month": date(year, month, 1).strftime("%b"), "hours_on_file": m["hours"],
                    "mean_temp_c": _r(sum(m["temps"]) / len(m["temps"]), 1) if m["temps"] else None,
                    "max_temp_c": _r(max(m["temps"]), 1) if m["temps"] else None,
                    "rain_mm": _r(m["rain"], 0), "sunshine_hours": _r(m["sun_s"] / 3600, 0)})
    return out


def season_weather(year: int) -> dict:
    spec = tuple((d["key"], *d["window_md"][0], *d["window_md"][1]) for d in DRIVERS)
    with Session(owner_engine) as owner:
        first = owner.exec(select(WeatherHistory.timestamp).order_by(WeatherHistory.timestamp)).first()
        last = owner.exec(select(WeatherHistory.timestamp).order_by(WeatherHistory.timestamp.desc())).first()
        if first is None:
            return {"error": "No weather on file yet - the Weather tab fetches it."}
        values = _weather_values(owner, spec, first.year, last.year)
        months = _month_summary(owner, year)
    if year not in values or not any(v is not None for v in values[year].values()) and not months:
        return {"error": f"No weather on file for {year}. Seasons on file: {first.year}-{last.year}."}
    factors = []
    for d in DRIVERS:
        k = d["key"]
        others = [v[k] for y, v in values.items() if y != year and v.get(k) is not None]
        factors.append({"factor": d["label"], "window": d["window_label"], "unit": d["unit"],
                        "value": _r(values.get(year, {}).get(k)),
                        "farm_average": _r(sum(others) / len(others)) if others else None,
                        "seasons_averaged": len(others)})
    return {"season": year, "weather_on_file_from": first.date().isoformat(),
            "weather_on_file_to": last.date().isoformat(), "factors": factors, "months": months}


def picking_pace(year: int, block_id: Optional[str] = None) -> dict:
    block_id = (block_id or "").strip() or None
    with Session(boord_engine) as boord, Session(owner_engine) as owner:
        s = season_day_kg(boord, owner)
    per_day: dict = {}
    for (y, bid, d), kg in s["day_kg"].items():
        if y == year and (block_id is None or bid == block_id) and kg > 0:
            per_day[d] = per_day.get(d, 0.0) + kg
    if not per_day:
        years = sorted({k[0] for k in s["day_kg"]})
        return {"error": f"No daily picking on file for {year}"
                         + (f" on block {block_id}" if block_id else "")
                         + f". Daily-tracked seasons: {', '.join(map(str, years))}."}
    timing = _season_timing({(y, bid, d): kg for (y, bid, d), kg in s["day_kg"].items()
                             if block_id is None or bid == block_id}, year)
    weeks: dict = {}
    for d, kg in per_day.items():
        iso = d.isocalendar()
        weeks[f"{iso[0]}-W{iso[1]:02d}"] = weeks.get(f"{iso[0]}-W{iso[1]:02d}", 0.0) + kg
    return {"season": year, "block": block_id or "whole farm", "in_progress": year == s["current_year"],
            "total_kg": _r(sum(per_day.values()), 0), "picking_days": len(per_day),
            "first_pick": timing["first_date"], "got_going": timing["start_date"],
            "last_pick": timing["last_date"], "span_days": timing["span_days"],
            "kg_per_week": [{"week": w, "kg": _r(k, 0)} for w, k in sorted(weeks.items())]}


# --------------------------------------------------------------------------- #
# The Boord Notes bridge
# --------------------------------------------------------------------------- #
def _post_json(url: str, body: dict, timeout: float) -> dict:
    """Split out so tests can stand in for Notes."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def notes_configured() -> bool:
    return bool(config.NOTES_URL)


def farm_notes(question: str) -> dict:
    """Boord Notes' own Ask: its answer, written from the notes only, and
    the notes it used (title and date; a link when NOTES_PUBLIC_URL says
    where phones open Notes). Raises RuntimeError in the owner's words."""
    if not config.NOTES_URL:
        raise RuntimeError("Boord Notes is not linked to this app (OWNER_NOTES_URL)")
    question = (question or "").strip()[:1000]
    if not question:
        raise RuntimeError("Type a question for the notes first")
    try:
        r = _post_json(f"{config.NOTES_URL}/api/ai/ask", {"question": question}, NOTES_TIMEOUT_S)
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            detail = json.loads(raw).get("detail")
        except ValueError:
            detail = None
        if e.code == 503 and isinstance(detail, str):
            raise RuntimeError(f"Boord Notes: {detail}") from None   # Notes' own sentence for Andre
        raise RuntimeError(f"Boord Notes answered HTTP {e.code}") from None
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Boord Notes could not be reached on the farm server ({e})") from None
    sources = [{"title": src.get("title") or "(untitled)", "date": str(src.get("created_at", ""))[:10],
                **({"url": f"{config.NOTES_PUBLIC_URL}/app/"} if config.NOTES_PUBLIC_URL else {})}
               for src in r.get("sources") or []]
    return {"answer": r.get("answer", ""), "sources": sources,
            "notes_considered": r.get("notes_considered"), "notes_total": r.get("notes_total")}


def run_tool(name: str, args: dict):
    """Dispatch for ai.stream_events. A bad name or argument raises; the
    model is told and carries on without it."""
    if name == "block_history":
        return block_history(str(args.get("block_id", "")))
    if name == "season_weather":
        return season_weather(int(args["year"]))
    if name == "picking_pace":
        return picking_pace(int(args["year"]), args.get("block_id"))
    if name == "farm_notes":
        return farm_notes(str(args.get("question", "")))
    raise ValueError(f"unknown tool {name}")
