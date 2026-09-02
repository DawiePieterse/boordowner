from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from db import get_boord_session, get_owner_session, own_farm_block_ids
from models_boord import Block, HarvestRecord, SystemSetting
from models_owner import HistoricalHarvest
from security import get_current_user
from timeutil import season_day, season_year_for, to_local

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _block_sort_key(block_id: str):
    """Natural block order (7, 8a, 8b, 9, 10a, 10b, 11, ...) instead of
    alphabetical, where "10a" sorts before "7". Block IDs are a number
    optionally followed by a letter suffix (see db.py's REAL_BLOCKS)."""
    block_id = block_id or ""
    num = "".join(ch for ch in block_id if ch.isdigit())
    suffix = block_id[len(num):] if num else block_id
    return (int(num) if num else 0, suffix)


@router.get("/summary")
def analysis_summary(boord: Session = Depends(get_boord_session),
                     owner: Session = Depends(get_owner_session),
                     user=Depends(get_current_user)):
    """Analysis data - see build_analysis_summary() for what it computes."""
    return build_analysis_summary(boord, owner)


def build_analysis_summary(boord: Session, owner: Session) -> dict:
    """Historical (2020-2025, from HistoricalHarvest) vs current-season
    (from HarvestRecord) comparisons for the Analysis tab: season pace,
    per-block/variety yield, season length, and monthly totals. Aggregated
    in Python over the full table - fine at this farm's data volume, and
    keeps the split-block/typo handling (baked into HistoricalHarvest at
    import time) out of SQL.

    Covers this farm's own blocks only (db.own_farm_block_ids) - Boord's
    block register can hold another grower's orchard, and HistoricalHarvest
    has history for this farm alone, so mixing the two would compare this
    season against a past that isn't its own.

    `boord` supplies SystemSetting / Block / HarvestRecord (Boord's live
    database, read-only); `owner` supplies HistoricalHarvest (this app's
    own database)."""
    settings = boord.exec(select(SystemSetting)).first()
    current_year = settings.current_harvest_year if settings else date.today().year
    # Where a season starts. Boord owns this (SystemSetting.season_start_month
    # /_day, v3.0 onward) and there is deliberately no fallback here: a second
    # opinion about the anchor is how the two apps end up disagreeing about
    # which season a date is in. A farm whose season runs Aug-Dec sets 1 August
    # in Boord's Settings; Boord's own default of 1 January reproduces the old
    # calendar-year behaviour exactly.
    anchor_month = settings.season_start_month if settings else 1
    anchor_day = settings.season_start_day if settings else 1
    # Only this farm's own blocks. Boord is a pack house now, so its block
    # register can hold another grower's orchard - and every comparison on
    # this tab is "this season against THIS farm's history", which the
    # history tables only have for this farm. See db.own_farm_block_ids.
    own_blocks = own_farm_block_ids(boord)
    blocks = {b.id: b for b in boord.exec(select(Block)).all() if b.id in own_blocks}

    # (season_year, block_id, harvest_date) -> kg, current season folded in
    # as just another year so every chart below treats it uniformly.
    day_kg: dict = defaultdict(float)
    estimated_blocks: set = set()  # blocks with at least one hectare-ratio-split historical row
    for h in owner.exec(select(HistoricalHarvest)).all():
        day_kg[(h.season_year, h.block_id, h.harvest_date)] += h.kg
        if h.estimated:
            estimated_blocks.add(h.block_id)
    for r in boord.exec(select(HarvestRecord)).all():
        local_ts = to_local(r.timestamp)
        if local_ts is None:
            continue
        # Which season this record belongs to, by the anchor - NOT by its
        # calendar year. The two differ for every anchor other than 1 January,
        # and a season anchored in August genuinely runs into the next year.
        #
        # Keeping this one test as the single gate is what keeps every panel
        # on the tab consistent. It used to be a calendar-year match followed
        # by a separate "is this before 1 August?" guard, and a record that
        # slipped between the two landed on a negative season_day: the
        # cumulative loop below silently dropped it (it starts at day 1) while
        # the monthly heatmap still counted it, so Season-to-Date and the
        # monthly grand total disagreed on the same screen. Such records still
        # appear in the date-range Dashboard and reports, so nothing is lost
        # from the app.
        if season_year_for(local_ts.date(), anchor_month, anchor_day) != current_year:
            continue
        if r.block_id not in own_blocks:
            continue
        day_kg[(current_year, r.block_id, local_ts.date())] += (r.weight_kg - r.deduction_kg)

    years = sorted({k[0] for k in day_kg})
    historical_years = [y for y in years if y != current_year]
    # Season Pace / Season Length / Monthly always reserve a row/entry for
    # the current season even before any of it has been picked, so those
    # charts don't reflow (new row, shifted colors) the moment picking
    # starts - it's just empty until then.
    all_years = sorted(set(years) | {current_year})

    # --- Season pace: cumulative kg by day-of-season ---
    # Every year's series starts at season_day 1 (the anchor date) even if
    # picking hadn't actually started yet - the cumulative total genuinely is
    # 0 kg at that point, so it's not a fabricated point, and it means every
    # line on the chart shares the same starting edge.
    year_day_totals: dict = defaultdict(lambda: defaultdict(float))
    for (year, block_id, d), kg in day_kg.items():
        year_day_totals[year][season_day(d, year, anchor_month, anchor_day)] += kg

    season_pace = []
    for year in all_years:
        days = sorted(year_day_totals[year])
        points = []
        if days:
            cum = 0.0
            for sd in range(1, days[-1] + 1):
                cum += year_day_totals[year].get(sd, 0.0)
                points.append({"season_day": sd, "cumulative_kg": round(cum, 1)})
        season_pace.append({"year": year, "is_current": year == current_year, "points": points})

    # 5-year historical average curve, aligned by season_day.
    #
    # A season that has already finished keeps contributing its FINAL
    # cumulative total for the rest of the axis, rather than dropping out of
    # the average once it runs out of days. Averaging only the still-running
    # seasons makes a cumulative curve fall as the shorter ones end - e.g.
    # with seasons ending on days 142-145, the average plunged from 302,328 kg
    # at day 142 to 237,846 at day 145, the latter being simply whichever
    # single season ran longest rather than an average at all. That also fed
    # pct_vs_average below, understating the baseline the current season is
    # judged against.
    avg_curve = []
    hist_series = [s for s in season_pace if not s["is_current"] and s["points"]]
    if hist_series:
        # Built once per series instead of once per (series, day) - the old
        # shape rebuilt every series' lookup dict inside the per-day loop.
        hist_pts = [{p["season_day"]: p["cumulative_kg"] for p in s["points"]} for s in hist_series]
        hist_finals = [s["points"][-1]["cumulative_kg"] for s in hist_series]
        last_day = max(s["points"][-1]["season_day"] for s in hist_series)
        for sd in range(1, last_day + 1):
            # Every series runs from day 1, so a miss here only ever means
            # "this season had already finished" - hence the final total.
            vals = [pts.get(sd, final) for pts, final in zip(hist_pts, hist_finals)]
            avg_curve.append({"season_day": sd, "cumulative_kg": round(sum(vals) / len(vals), 1)})

    current_series = next((s for s in season_pace if s["is_current"]), None)
    season_to_date_kg = current_series["points"][-1]["cumulative_kg"] if current_series and current_series["points"] else 0.0
    avg_by_day = {p["season_day"]: p["cumulative_kg"] for p in avg_curve}
    avg_at_same_point = avg_by_day.get(current_series["points"][-1]["season_day"]) if current_series and current_series["points"] else None
    pct_vs_average = round((season_to_date_kg - avg_at_same_point) / avg_at_same_point * 100, 1) \
        if avg_at_same_point else None

    # --- Per-block yield trends -------------------------------------------
    block_year_kg: dict = defaultdict(lambda: defaultdict(float))
    for (year, block_id, d), kg in day_kg.items():
        if block_id:
            block_year_kg[block_id][year] += kg

    # by_year carries kg/kg_ha/kg_tree for every year including the current
    # one - the frontend derives "this season vs historical average" from it
    # for whichever metric (kg/ha or kg/tree) is selected, rather than the
    # API baking in one metric's comparison ahead of time.
    block_yield = []
    for block_id, by_year in block_year_kg.items():
        b = blocks.get(block_id)
        by_year_out = {}
        for year, kg in by_year.items():
            kg_ha = round(kg / b.hectares, 1) if b and b.hectares else None
            kg_tree = round(kg / b.trees, 1) if b and b.trees else None
            by_year_out[year] = {"kg": round(kg, 1), "kg_ha": kg_ha, "kg_tree": kg_tree}
        block_yield.append({
            "block_id": block_id, "name": b.name if b else block_id, "variety": b.variety if b else "",
            "hectares": b.hectares if b else None, "trees": b.trees if b else None,
            "by_year": by_year_out, "estimated": block_id in estimated_blocks,
        })
    block_yield.sort(key=lambda x: _block_sort_key(x["block_id"]))

    # --- Variety performance over time ------------------------------------
    variety_blocks: dict = defaultdict(set)
    for block_id, b in blocks.items():
        if b.variety:
            variety_blocks[b.variety].add(block_id)

    variety_year_kg: dict = defaultdict(lambda: defaultdict(float))
    for (year, block_id, d), kg in day_kg.items():
        b = blocks.get(block_id)
        if b and b.variety:
            variety_year_kg[b.variety][year] += kg

    variety_yield = []
    for variety, by_year in variety_year_kg.items():
        total_ha = sum((blocks[bid].hectares or 0) for bid in variety_blocks.get(variety, []))
        total_trees = sum((blocks[bid].trees or 0) for bid in variety_blocks.get(variety, []))
        by_year_out = {
            year: {
                "kg": round(kg, 1),
                "kg_ha": round(kg / total_ha, 1) if total_ha else None,
                "kg_tree": round(kg / total_trees, 1) if total_trees else None,
            }
            for year, kg in by_year.items()
        }
        variety_yield.append({"variety": variety, "hectares": round(total_ha, 1) if total_ha else None,
                               "trees": total_trees or None, "by_year": by_year_out})
    variety_yield.sort(key=lambda x: x["variety"])

    # --- Season length: first pick -> last pick, per year ------------------
    year_pick_days: dict = defaultdict(set)  # year -> set of season_days with kg > 0
    for (year, block_id, d), kg in day_kg.items():
        if kg > 0:
            year_pick_days[year].add(season_day(d, year, anchor_month, anchor_day))

    season_length = []
    for year in all_years:
        days = year_pick_days.get(year)
        season_length.append({
            "year": year, "is_current": year == current_year,
            "first_day": min(days) if days else None,
            "last_day": max(days) if days else None,
            "span_days": (max(days) - min(days) + 1) if days else None,
            "pick_days": len(days) if days else 0,
        })

    # --- Monthly totals per year, for the Monthly Harvest heatmap ---------
    # Each cell carries both its kg and its share of that season's total, so
    # the heatmap can show "which months matter" in absolute and relative
    # terms at once.
    year_month_kg: dict = defaultdict(lambda: defaultdict(float))
    for (year, block_id, d), kg in day_kg.items():
        year_month_kg[year][d.month] += kg

    monthly = []
    for year in all_years:
        months = year_month_kg[year]
        year_total = sum(months.values())
        monthly.append({
            "year": year, "is_current": year == current_year,
            "total_kg": round(year_total, 1),
            "by_month": {
                date(2001, m, 1).strftime("%b"): {
                    "kg": round(kg, 1),
                    "pct": round(kg / year_total * 100, 1) if year_total else None,
                }
                for m, kg in months.items()
            },
        })

    return {
        "current_year": current_year,
        # The charts draw a season_day axis and a month order, both of which
        # only mean anything against the anchor they were computed from -
        # so it travels with the data rather than being fetched separately.
        "season_anchor": {"month": anchor_month, "day": anchor_day},
        "historical_years": historical_years,
        "season_to_date_kg": round(season_to_date_kg, 1),
        "pct_vs_average": pct_vs_average,
        "season_pace": {"years": season_pace, "average": avg_curve},
        "block_yield": block_yield,
        "variety_yield": variety_yield,
        "season_length": season_length,
        "monthly": monthly,
    }
