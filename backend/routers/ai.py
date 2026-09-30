"""Ask about this estimate: questions in plain words about the Estimate tab,
answered by an AI model from the tab's own figures.

The same shape as the Weather Compare app's "Ask about this comparison"
(its site/js/insights.js), moved server-side: the figures are summarised
here, from the same functions the tab renders (estimate_view,
build_analogs), and the provider key never leaves the farm server (see
ai.py and config.AI_*).

What the model is for, and what it is not:

  * It explains and flags - which blocks sit outside their own history,
    how the estimate compares with the weather model and the similar
    seasons, how the season is tracking, what moved between versions.
  * It never writes to an estimate. The owner's judgement per block stays
    the record; an answer is only ever read (or pasted into the notes by
    the owner).
  * It sees only what the tab shows. The summary sent is built from the
    tab's own figures plus a few precomputed "highlights" (a small model
    finds a block outside its range far more reliably when told), and the
    prompt holds it to those figures.

Unsaved edits count: the browser sends its working copy of the estimate
(`draft`) and the weather-model figures it is showing (`forecast`, as a save
would), so "review this before I save it" reviews what is on screen.

Boord's database is read and released before the provider is called - the
model can take tens of seconds, and a Boord read must never be held open
across an outbound call (db.get_boord_session).
"""
import json
from datetime import date
from types import SimpleNamespace
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field as PydField
from sqlmodel import Session

import ai
from db import boord_engine, owner_engine
from routers.analogs import build_analogs
from routers.estimate import (EstimateForecastIn, EstimateLineIn, PackLineIn, _crosscheck,
                              _packout, _r, estimate_view)

router = APIRouter(prefix="/api/ai", tags=["ai"])

HISTORY_TURNS = 3          # earlier question/answer pairs sent with a follow-up
FAR_FROM_SIMILAR = 0.25    # an estimate this far (either way) from its similar-seasons figure is flagged
FARM_HISTORY_SEASONS = 12  # whole-farm seasons sent, most recent first


SYSTEM_PROMPT = """You are a careful assistant helping a fruit farm's owner with their crop estimate for one season.

You will receive a JSON summary of the app's Estimate tab:
- context: the season, today's date, and whether the estimate shown is saved
- estimate: the owner's totals, and their own notes
- blocks: per block, the owner's kg/tree estimate beside that block's own history (last season, the last-5 average, the best 5 of the last 10, the 10-season low-high), the similar-seasons figure, and kg picked so far
- weather_model: the Risk tab's weather-driven Harvest Forecast (favorable / expected / unfavorable) and where the estimate sits against it
- similar_seasons: past seasons whose weather so far came closest, and what they produced
- progress: kg picked so far and the in-season projection (current season only)
- pack_out, versions, farm_history
- highlights: notable facts already worked out from the figures above

Rules:
- Use ONLY the figures in the JSON. Never invent or estimate a missing figure; say it is not on file.
- The estimate is the owner's judgement from walking the orchard. Explain, compare and flag; do not overrule it. If asked for a figure, give a range with the history behind it and leave the call to the owner.
- Always give units: kg, t (tonnes) or kg/tree. Whole kg; tonnes to one decimal.
- Name blocks as the JSON does, e.g. "block 8a".
- The weather model explains only about a quarter to a half of this farm's swing from season to season, and the similar seasons are a range, not a forecast. A gap between either and the estimate is a reason to look again in the orchard, not proof that either is wrong.
- History kg/tree uses each block's current tree count for every season. Seasons listed in annual_only_years were recorded as season totals, not day by day.
- Be concise: short paragraphs or bullet points, most important first. No JSON or code."""

USER_TEMPLATE = """Here are the Estimate tab's figures:

{summary}

Question: {question}

Answer from the figures above only."""


class DraftIn(BaseModel):
    """The browser's working copy of the shown version, unsaved edits and all.
    Validated like a save (EstimateUpdate), so it can only say what a save
    could."""
    name: str = PydField(default="", max_length=100)
    notes: str = PydField(default="", max_length=4000)
    lines: List[EstimateLineIn] = PydField(default_factory=list, max_length=1000)
    pack: List[PackLineIn] = PydField(default_factory=list, max_length=200)


class TurnIn(BaseModel):
    q: str = PydField(max_length=1000)
    a: str = PydField(max_length=10000)


class AskIn(BaseModel):
    tab: Literal["estimate"] = "estimate"
    season: Optional[int] = None
    estimate_id: Optional[int] = None
    draft: Optional[DraftIn] = None
    forecast: Optional[EstimateForecastIn] = None
    question: str = PydField(min_length=1, max_length=1000)
    history: List[TurnIn] = PydField(default_factory=list, max_length=20)


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #
def _pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b else None


def _block_rows(lines: list, refs: dict, analog_blocks: dict, current: bool) -> list:
    rows = []
    for l in lines:
        bid = l["block_id"]
        ref = refs.get(bid) or {}
        kg_tree = l.get("kg_per_tree")
        trees = l.get("trees") or 0
        kg = trees * kg_tree if kg_tree is not None else None
        history = ref.get("history") or {}
        similar = analog_blocks.get(bid)
        row = {
            "block": bid,
            "variety": ref.get("variety"),
            "trees": trees,
            "estimate_kg_tree": kg_tree,
            "estimate_kg": _r(kg, 0),
            "vs_last_pct": _pct(kg_tree, ref.get("last_kg_tree")),
            "vs_avg5_pct": _pct(kg_tree, ref.get("avg5_kg_tree")),
            "last_kg_tree": ref.get("last_kg_tree"),
            "avg5_kg_tree": ref.get("avg5_kg_tree"),
            "best5_kg_tree": ref.get("best5_kg_tree"),
            "low_kg_tree": ref.get("low_kg_tree"),
            "high_kg_tree": ref.get("high_kg_tree"),
            "seasons_on_file": ref.get("seasons_on_file", 0),
            "history_kg_tree": {str(y): h.get("kg_tree") for y, h in sorted(history.items(), key=lambda t: int(t[0]))},
            "annual_only_years": sorted(int(y) for y, h in history.items() if h.get("annual_only")),
            "similar_seasons_kg_tree": similar,
            "note": (l.get("note") or "").strip() or None,
        }
        if ref.get("trees") is not None and ref.get("trees") != trees:
            row["register_trees"] = ref.get("trees")
        if not ref:
            row["not_in_register"] = True
        if current:
            picked = ref.get("actual_kg")
            row["picked_kg"] = _r(picked, 0)
            row["picked_pct_of_estimate"] = round(picked / kg * 100, 1) if picked is not None and kg else None
        rows.append(row)
    return rows


def _highlights(blocks: list, totals: dict, pack_out: Optional[dict], weather: Optional[dict],
                progress: Optional[dict]) -> list:
    """The facts a reader checks first, worked out rather than left for the
    model to find."""
    out = []
    missing = [b["block"] for b in blocks if b["estimate_kg_tree"] is None]
    if missing:
        out.append(f"No kg/tree estimate yet for block(s) {', '.join(missing)}.")
    for b in blocks:
        v = b["estimate_kg_tree"]
        if v is None:
            continue
        if b["high_kg_tree"] is not None and v > b["high_kg_tree"]:
            out.append(f"Block {b['block']}: {v} kg/tree is above its 10-season high of {b['high_kg_tree']}.")
        elif b["low_kg_tree"] is not None and v < b["low_kg_tree"]:
            out.append(f"Block {b['block']}: {v} kg/tree is below its 10-season low of {b['low_kg_tree']}.")
        if not b["seasons_on_file"]:
            out.append(f"Block {b['block']} has no finished season on file to compare with.")
        s = b["similar_seasons_kg_tree"]
        if s and abs(v / s - 1) > FAR_FROM_SIMILAR:
            out.append(f"Block {b['block']}: {v} kg/tree is {_pct(v, s):+.0f}% against the similar seasons' {s} kg/tree.")
    moves = sorted((b for b in blocks if b["vs_last_pct"] is not None), key=lambda b: b["vs_last_pct"])
    if moves:
        lo, hi = moves[0], moves[-1]
        if hi["vs_last_pct"] > 0:
            out.append(f"Largest rise on last season: block {hi['block']} ({hi['vs_last_pct']:+.0f}%).")
        if lo["vs_last_pct"] < 0:
            out.append(f"Largest drop on last season: block {lo['block']} ({lo['vs_last_pct']:+.0f}%).")
    if totals.get("vs_last_season_pct") is not None:
        out.append(f"Estimate total is {totals['vs_last_season_pct']:+.1f}% on last season's {totals['last_season_kg']:,.0f} kg.")
    if weather and weather.get("position") and weather.get("gap_pct") is not None:
        out.append(f"Estimate is {weather['position']} the weather model's range "
                   f"({weather['gap_pct']:+.1f}% against its expected {weather['expected_kg']:,.0f} kg).")
    if pack_out and abs(pack_out["unallocated_pct"]) > 0.05:
        out.append(f"Pack-out shares add up to {pack_out['allocated_pct']}%, not 100%.")
    if progress and progress.get("expected_by_now_kg"):
        out.append(f"Picked so far {progress['actual_kg']:,.0f} kg against {progress['expected_by_now_kg']:,.0f} kg "
                   f"the estimate implies by now ({_pct(progress['actual_kg'], progress['expected_by_now_kg']):+.0f}%).")
    return out


def _similar(analogs: Optional[dict]) -> Optional[dict]:
    if not analogs:
        return None
    out = {"state": analogs["state"]}
    if analogs["state"] != "ok":
        return out
    out["factors"] = [{"factor": f["label"], "window": f["window"], "status": f["status"],
                       "compared_until": f["compared_until"]}
                      for f in analogs["factors"] if f["weight"]]
    out["seasons"] = [{"year": a["year"], "closeness": a["closeness"], "distance": a["distance"],
                       "record": a["record"], "kg": _r(a["kg"], 0), "vs_its_era_avg_pct": a["vs_avg_pct"],
                       "among_closest": a["in_top"],
                       "picking_started": (a["timing"] or {}).get("start_date"),
                       "picking_span_days": (a["timing"] or {}).get("span_days")}
                      for a in analogs["analogs"]]
    out["closest_spread_pct"] = analogs["spread"]
    out["block_figures_from_years"] = analogs["block_basis_years"]
    return out


def build_estimate_summary(boord: Session, owner: Session, body: AskIn, today: date) -> dict:
    """Everything the model is told, as JSON-ready data. Also returns, under
    "_check", the seasons and blocks it could fairly name - the browser flags
    an answer that names any other."""
    view = estimate_view(season=body.season, estimate_id=body.estimate_id, boord=boord, owner=owner)
    try:
        analogs = build_analogs(boord, owner, view["season_year"], today)
    except Exception as e:  # noqa: BLE001 - the similar seasons are one part of many
        print(f"[ai] similar seasons unavailable: {e}", flush=True)
        analogs = None
    boord.close()

    season_year, current_year = view["season_year"], view["current_year"]
    current = season_year == current_year
    est = view["estimate"]
    refs = {b["block_id"]: b for b in view["blocks"]}

    if body.draft is not None and est is not None:
        name, notes = body.draft.name, body.draft.notes
        lines = [l.model_dump() for l in body.draft.lines]
        pack = [p.model_dump() for p in body.draft.pack]
    elif est is not None:
        name, notes, lines, pack = est["name"], est["notes"], est["lines"], est["pack"]
    else:
        name, notes, lines, pack = None, "", [], []

    blocks = _block_rows(lines, refs, (analogs or {}).get("blocks") or {}, current)
    total = sum(b["estimate_kg"] or 0 for b in blocks)
    last = next((h for h in view["farm_history"] if h["year"] == season_year - 1 and not h["partial"]), None)
    totals = {
        "total_kg": _r(total, 0),
        "total_trees": sum(b["trees"] for b in blocks),
        "blocks_estimated": sum(1 for b in blocks if b["estimate_kg_tree"] is not None),
        "blocks_in_estimate": len(blocks),
        "last_season_kg": _r(last["kg"], 0) if last else None,
        "vs_last_season_pct": _pct(total, last["kg"]) if last and total else None,
    }
    left_out = [bid for bid, r in refs.items() if r["active"] and bid not in {l["block_id"] for l in lines}]

    pack_rows = [SimpleNamespace(**{"position": i, **p}) for i, p in enumerate(pack)]
    pack_out = _packout(total, pack_rows) if est is not None else None
    if pack_out:
        pack_out = {k: pack_out[k] for k in ("channels", "cartons", "avg_kg_per_carton",
                                             "allocated_pct", "unallocated_pct", "unallocated_kg")}

    # The weather model: live figures the tab is showing (current season),
    # else what it said when the shown version was saved.
    weather = None
    f = body.forecast
    if current and f is not None and f.season_year == season_year:
        weather = {"as_of": f.built_at.isoformat(), "source": "live",
                   "favorable_kg": f.favorable_kg, "expected_kg": f.expected_kg,
                   "unfavorable_kg": f.unfavorable_kg, "live_weather_forecast_used": f.live,
                   "factors_settled": f.settled}
    elif est is not None and est.get("forecast_snapshot"):
        snap = est["forecast_snapshot"]
        weather = {"as_of": snap["built_at"], "source": "saved with this version",
                   "favorable_kg": snap["favorable_kg"], "expected_kg": snap["expected_kg"],
                   "unfavorable_kg": snap["unfavorable_kg"], "live_weather_forecast_used": snap["live"],
                   "factors_settled": snap["settled"]}
    if weather:
        weather["factor_count"] = 4
        weather.update(_crosscheck(total, weather["favorable_kg"], weather["expected_kg"],
                                   weather["unfavorable_kg"]) or {"gap_kg": None, "gap_pct": None, "position": None})

    progress = None
    p = view["progress"]
    if p:
        progress = {k: p[k] for k in ("season_day", "actual_kg", "typical_share", "projected_kg",
                                      "projected_low_kg", "projected_high_kg", "range_years")}
        progress["expected_by_now_kg"] = (_r(total * p["typical_share"], 0)
                                          if total and p["typical_share"] is not None else None)

    versions = [{"name": v["name"], "saved": v["updated_at"][:10], "total_kg": _r(v["total_kg"], 0),
                 "weather_model_expected_kg": (v["forecast"] or {}).get("expected_kg"),
                 "vs_weather_model": (v["forecast"] or {}).get("position"),
                 "shown": est is not None and v["id"] == est["id"]}
                for v in reversed(view["estimates"])]   # oldest first: how the call moved

    farm_history = [{"year": h["year"], "kg": _r(h["kg"], 0), **({"partial": True} if h["partial"] else {})}
                    for h in view["farm_history"][-FARM_HISTORY_SEASONS:]]
    similar = _similar(analogs)

    summary = {
        "context": {
            "today": today.isoformat(),
            "season_year": season_year,
            "current_season": current_year,
            "season_status": ("being picked now" if current else
                              "not started yet" if season_year > current_year else "finished"),
            "estimate_name": name,
            "has_estimate": est is not None,
            "unsaved_changes_included": body.draft is not None and est is not None,
            "units": "kg are net picked kg; t = tonnes; kg/tree on each block's current tree count",
        },
        "estimate": {**totals, "notes": (notes or "").strip() or None,
                     "active_blocks_left_out": left_out},
        "blocks": blocks,
        "weather_model": weather,
        "similar_seasons": similar,
        "progress": progress,
        "season_total_so_far" if current else "season_total": view["season_total"],
        "pack_out": pack_out,
        "versions": versions,
        "farm_history": farm_history,
    }
    summary["highlights"] = _highlights(blocks, totals, pack_out, weather, progress)

    years = {season_year, current_year, *(h["year"] for h in view["farm_history"])}
    years.update(int(y) for b in blocks for y in b["history_kg_tree"])
    years.update(s["year"] for s in (similar or {}).get("seasons", []))
    years.update(progress["range_years"] if progress else [])
    summary["_check"] = {"years": sorted(years), "blocks": sorted({*refs, *(l["block_id"] for l in lines)})}
    return summary


def build_messages(summary: dict, question: str, history: list) -> list:
    """The figures go once, with the first question; follow-ups carry only
    their text, with the earlier answers in between."""
    data = {k: v for k, v in summary.items() if not k.startswith("_")}
    turns = [*history[-HISTORY_TURNS:], {"q": question, "a": None}]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for i, t in enumerate(turns):
        q = t["q"].strip()
        messages.append({"role": "user", "content": USER_TEMPLATE.format(
            summary=json.dumps(data, separators=(",", ":"), default=str), question=q)
            if i == 0 else f"Question: {q}"})
        if t["a"] is not None:
            messages.append({"role": "assistant", "content": t["a"]})
    return messages


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get("/status")
def ai_status():
    """Whether Ask is set up, and with what - the tab shows a setup hint
    instead of the question box when it isn't."""
    s = ai.settings()
    if not s:
        return {"configured": False}
    return {"configured": True, "provider": ai.provider_name(s), "model": ai.model_for(s)}


def _answer(messages: list, s: dict, check: dict):
    """NDJSON: {"t": text} per piece as it arrives, then {"done": ...} or
    {"error": message}. A refusal mid-answer still reaches the browser as
    words - the HTTP status went out with the first line."""
    try:
        for text in ai.stream(messages, s):
            yield json.dumps({"t": text}) + "\n"
        yield json.dumps({"done": True, "provider": ai.provider_name(s),
                          "model": ai.model_for(s), **check}) + "\n"
    except ai.AIError as e:
        yield json.dumps({"error": str(e)}) + "\n"


@router.post("/ask")
def ask(body: AskIn):
    s = ai.settings()
    if not s:
        raise HTTPException(503, "Ask isn't set up on the farm server (see README, Ask about this estimate)")
    # Both databases are read and closed before the first byte goes to the
    # provider - explicit sessions rather than Depends, so that is plain to see.
    with Session(owner_engine) as owner, Session(boord_engine) as boord:
        summary = build_estimate_summary(boord, owner, body, date.today())
    history = [t.model_dump() for t in body.history]
    messages = build_messages(summary, body.question, history)
    return StreamingResponse(_answer(messages, s, summary["_check"]), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store"})
