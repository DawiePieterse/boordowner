#!/usr/bin/env python3
"""Import of the farm's hand-kept litchi "Markte" market-statement workbook
into MarketPackPrice - price per carton by buyer and pack type, read
straight off each season's own "<year> Totaal" sheet rather than a fixed
column layout (a buyer or pack type can be added from one season's
workbook to the next, so the pack-type columns are found by their own
SEND/SOLD/OUTSTANDING/PRICE/SUB headers, not a hardcoded index).

Source: a per-season workbook such as
data/imports/LIETJIES_Markte_2025_SEISOEN.xlsx (gitignored - per-farm data,
not shipped with the app). One workbook can hold several "<year> Totaal"
sheets at once (the farm's own habit of keeping last season's total
alongside this one's, for comparison) - every one found is imported.

The per-receiver sheets beside them (ToroFruit, Dapper, AgriLink USA
Lug...: a row per delivery, the same pack-type column groups) go into
MarketDailySale, for the Markets tab's daily price chart. They are this
workbook's own season - the newest of its Totaal sheets - and any sheet
with the SEND/SOLD/... headers counts; one with no priced lines at all
(ProForma, a sheet still waiting on its statements) simply adds nothing.

Safe to re-run: replaces only the season_year(s) the workbook actually has
a Totaal sheet for, so importing next season's workbook never touches an
earlier season that was imported from a different file.

Usage (run with the backend's own venv so sqlmodel etc. are on the path):
    backend/.venv/bin/python3 scripts/import_market_prices.py <path-to-workbook.xlsx>
"""
import os
import re
import sys
from datetime import date, datetime

import openpyxl

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from sqlmodel import Session, delete  # noqa: E402

from db import init_owner_db as run_migrations, owner_engine as engine  # noqa: E402
from models_owner import MarketDailySale, MarketPackPrice  # noqa: E402

_SHEET_RE = re.compile(r"^(\d{4})\s+totaal$", re.IGNORECASE)

# Pack-type names the workbook and Boord Packhouse's own Settings ->
# Consignment list never agreed on at all - an Afrikaans word ("Kratte" is
# "crate"), or the farm's own nickname for a size ("Rhino" for the 4.5kg
# XL carton). routers/market.py's _pack_key() already bridges case and
# punctuation when matching against Packhouse's live list, but it can only
# ever match the same word differently spelled - it has no way to know
# these are the same pack type at all. Same shape as block_renames.py: a
# record of this one farm's own naming, not a general mechanism. Add to
# this map rather than editing the workbooks, which are the farm's own
# record and should keep saying what they said when they were written.
PACK_RENAMES = {
    "15 Kg Kratte": "15kg Crate",
    "4.5kg XL": "4.5kg Rhino",
}

# Per-receiver sheets that are one receiver to the farm. The office kept
# AgriLink's 2kg air-freight boxes on a sheet of their own, but they are the
# same AgriLink USA air channel as the "AgriLink USA Lug" sheet, so they are
# charted and exported as one receiver - a day on both sheets becomes one
# point, its price weighted by cartons (routers/market.py _daily). Same
# shape and same rule as PACK_RENAMES.
RECEIVER_RENAMES = {
    "AgriLink 2kg Lug": "AgriLink USA Lug",
}


def _clean(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _pack_type(label_cell, season_year: int) -> str:
    """The group label, with a leading "<season_year> " stripped - one
    season's sheet (2023) wrote its pack-type headers as "2023 2kg XL"
    rather than "2kg XL", which would otherwise stop that season's own
    pack types from matching the same pack type in any other season - and
    passed through PACK_RENAMES."""
    label = _clean(label_cell)
    prefix = f"{season_year} "
    if label.startswith(prefix):
        label = label[len(prefix):]
    return PACK_RENAMES.get(label, label)


def _group_starts(header_row: list) -> list:
    """Index of each pack-type column group's SEND cell - wherever the
    SEND/SOLD/OUTSTANDING/PRICE/SUB headers repeat, not a fixed offset, so
    an extra pack-type column doesn't silently shift anything out of
    alignment."""
    return [i for i, v in enumerate(header_row) if v == "SEND"]


def _season_rows(ws, season_year: int) -> list:
    label_row = [c.value for c in next(ws.iter_rows(min_row=2, max_row=2))]   # pack-type name
    header_row = [c.value for c in next(ws.iter_rows(min_row=3, max_row=3))]  # SEND/SOLD/.../SUB
    groups = [(i, _pack_type(label_row[i], season_year)) for i in _group_starts(header_row)]
    rows = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        buyer = _clean(row[1]) if len(row) > 1 else ""
        if not buyer or "total" in buyer.lower() or "totaal" in buyer.lower():
            continue
        for start, pack_type in groups:
            if not pack_type:
                continue
            sold = row[start + 1] if start + 1 < len(row) else None
            price = row[start + 3] if start + 3 < len(row) else None
            sub = row[start + 4] if start + 4 < len(row) else None
            if not isinstance(sold, (int, float)) or not sold:
                continue
            if not isinstance(price, (int, float)) or not price:
                continue  # '#DIV/0!' (a formula error) or genuinely 0 - neither is a price
            rows.append(MarketPackPrice(
                season_year=season_year, buyer=buyer, pack_type=pack_type,
                cartons_sold=float(sold), price_per_carton=float(price),
                sub_total=float(sub) if isinstance(sub, (int, float)) else 0.0,
            ))
    return rows


def _sale_date(cell):
    """The Datum column: typed as text, "07.11.25" (day.month.2-digit
    year), in every sheet so far - a real date cell is taken as-is."""
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    try:
        return datetime.strptime(_clean(cell), "%d.%m.%y").date()
    except ValueError:
        return None


def _daily_rows(ws, receiver: str, season_year: int) -> list:
    """A per-receiver sheet's priced lines. Its header row is found by its
    SEND cells within the first few rows, with the pack-type names on the
    row above - the sheets don't all start on the same row ("Andre" is a
    row higher than the rest)."""
    top = [[c.value for c in r] for r in ws.iter_rows(min_row=1, max_row=6)]
    hdr = next((i for i, r in enumerate(top) if "SEND" in r), None)
    if not hdr:
        return []
    groups = [(i, _pack_type(top[hdr - 1][i], season_year)) for i in _group_starts(top[hdr])]
    rows = []
    for row in ws.iter_rows(min_row=hdr + 2, values_only=True):
        sale_date = _sale_date(row[0])
        if sale_date is None:
            continue
        for start, pack_type in groups:
            sold = row[start + 1] if start + 1 < len(row) else None
            price = row[start + 3] if start + 3 < len(row) else None
            if not pack_type or not isinstance(sold, (int, float)) or not sold:
                continue
            if not isinstance(price, (int, float)) or not price:
                continue
            rows.append(MarketDailySale(
                season_year=season_year, sale_date=sale_date, receiver=receiver,
                note_no=_clean(row[3]) if len(row) > 3 else "", pack_type=pack_type,
                cartons_sold=float(sold), price_per_carton=float(price),
            ))
    return rows


def load_rows(xlsx_path: str) -> tuple:
    """({season_year: [MarketPackPrice, ...]} for every "<year> Totaal"
    sheet, (season_year, [MarketDailySale, ...]) for the per-receiver
    sheets) - the daily season is the newest Totaal year, None without one."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    by_season = {}
    for name in wb.sheetnames:
        m = _SHEET_RE.match(name.strip())
        if m:
            season_year = int(m.group(1))
            by_season[season_year] = _season_rows(wb[name], season_year)
    daily_season = max(by_season) if by_season else None
    daily = []
    if daily_season is not None:
        for name in wb.sheetnames:
            if not _SHEET_RE.match(name.strip()):
                receiver = RECEIVER_RENAMES.get(_clean(name), _clean(name))
                daily += _daily_rows(wb[name], receiver, daily_season)
    return by_season, (daily_season, daily)


def main():
    if len(sys.argv) < 2:
        print("Usage: import_market_prices.py <path-to-workbook.xlsx>\n"
              "Reads every '<year> Totaal' sheet in the workbook - a farm's own\n"
              "hand-kept litchi market-statement file, not shipped with the app.")
        return
    xlsx_path = sys.argv[1]
    if not os.path.exists(xlsx_path):
        print(f"No such file: {xlsx_path}")
        return
    by_season, (daily_season, daily) = load_rows(xlsx_path)
    if not by_season:
        print(f"No '<year> Totaal' sheet found in {xlsx_path} - nothing imported.")
        return
    receivers = len({d.receiver for d in daily})   # before the commit expires them
    run_migrations()
    with Session(engine) as session:
        for season_year, rows in by_season.items():
            session.exec(delete(MarketPackPrice).where(MarketPackPrice.season_year == season_year))
            session.add_all(rows)
        session.exec(delete(MarketDailySale).where(MarketDailySale.season_year == daily_season))
        session.add_all(daily)
        session.commit()
    total = sum(len(r) for r in by_season.values())
    print(f"Imported {total} market-price rows across {len(by_season)} season(s) "
          f"({', '.join(str(y) for y in sorted(by_season))}), and {len(daily)} daily sales "
          f"for {daily_season} from {receivers} receivers, from:\n  {xlsx_path}")


if __name__ == "__main__":
    main()
