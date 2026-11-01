#!/usr/bin/env python3
"""Import of the farm's market history workbook, "Markte History", into
MarketDailySale (a row per delivery note and pack type, for the Markets
tab's daily price chart) and MarketPackPrice (each season's totals by
receiver and pack type, for its table).

The workbook is the one clean copy of the seasons before Boord kept market
records - 2022 to 2025, built once from the office's per-season "LIETJIES
Markte" workbooks by scripts/convert_market_workbooks.py, and corrected by
hand since. It is per-farm data, gitignored, and lives in data/imports/.
Seasons after it need no workbook: routers/market.py reads them live from
Boord Packhouse and Boord.

It reads the "Sales" sheet, its columns found by their headings
(convert_market_workbooks.SALES_COLUMNS), so a column the office adds or
moves changes nothing. Each row is one receiver's sale of one pack type on
one delivery note. The season totals are added up from the rows, weighted
by cartons, rather than typed separately: one set of figures, so the table
and the chart can't disagree. A row with no date counts in its season's
total but has no point on the chart. A row missing its season, receiver,
pack type, cartons or price is left out, and the import says which.

Safe to re-run: replaces every season the file has rows for, and no other.

Usage (run with the backend's own venv so sqlmodel etc. are on the path):
    backend/.venv/bin/python3 scripts/import_market_prices.py "data/imports/Markte History.xlsx"
"""
import os
import sys
from collections import defaultdict
from datetime import date, datetime

import openpyxl

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from sqlmodel import Session, delete  # noqa: E402

from db import init_owner_db as run_migrations, owner_engine as engine  # noqa: E402
from models_owner import MarketDailySale, MarketPackPrice  # noqa: E402

# The headings read, by what they hold. The rest of the sheet (Gross, From,
# Note) is the office's, not the app's.
_COLUMNS = {"season": "season", "date": "date", "receiver": "receiver", "note_no": "delivery note",
            "pack_type": "pack type", "cartons": "cartons sold", "price": "r per carton"}


def _clean(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _date(cell):
    """A date cell as written by convert_market_workbooks.py, or a date the
    office typed over it as "26/09/2023" or "26.09.2023"; None for a blank
    one, or anything else (reported)."""
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(_clean(cell), fmt).date()
        except ValueError:
            pass
    return None


def _number(cell):
    return float(cell) if isinstance(cell, (int, float)) and not isinstance(cell, bool) else None


def load_rows(xlsx_path: str) -> tuple:
    """({season: [MarketDailySale with a date, ...]}, {season: [MarketPackPrice, ...]},
    [problems]) from the workbook's Sales sheet. Nothing is stored."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if "Sales" not in wb.sheetnames:
        return {}, {}, [f"No 'Sales' sheet in {xlsx_path}."]
    ws = wb["Sales"]
    heads = [_clean(c.value).lower() for c in next(ws.iter_rows(min_row=1, max_row=1))]
    missing = [h for h in _COLUMNS.values() if h not in heads]
    if missing:
        return {}, {}, [f"The Sales sheet has no {', '.join(repr(h) for h in missing)} column."]
    col = {key: heads.index(h) for key, h in _COLUMNS.items()}

    daily, problems = defaultdict(list), []
    acc = defaultdict(lambda: [0.0, 0.0])   # (season, receiver, pack) -> [cartons, gross]
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        get = lambda key: row[col[key]] if col[key] < len(row) else None
        if not any(v not in (None, "") for v in row):
            continue
        season, cartons, price = _number(get("season")), _number(get("cartons")), _number(get("price"))
        receiver, pack = _clean(get("receiver")), _clean(get("pack_type"))
        if not season or not cartons or not price or not receiver or not pack:
            problems.append(f"Sales row {n}: needs a season, receiver, pack type, cartons and price - left out.")
            continue
        season = int(season)
        sale_date = _date(get("date"))
        if sale_date is None and _clean(get("date")):
            problems.append(f"Sales row {n}: date {_clean(get('date'))!r} isn't a date - "
                            "in the season total, not on the chart.")
        a = acc[(season, receiver, pack)]
        a[0] += cartons
        a[1] += cartons * price
        if sale_date is not None:
            daily[season].append(MarketDailySale(
                season_year=season, sale_date=sale_date, receiver=receiver, note_no=_clean(get("note_no")),
                pack_type=pack, cartons_sold=cartons, price_per_carton=price))

    totals = defaultdict(list)
    for (season, receiver, pack), (cartons, gross) in sorted(acc.items()):
        totals[season].append(MarketPackPrice(
            season_year=season, buyer=receiver, pack_type=pack, cartons_sold=round(cartons, 2),
            price_per_carton=round(gross / cartons, 2), sub_total=round(gross, 2)))
    return dict(daily), dict(totals), problems


def main():
    if len(sys.argv) < 2:
        print('Usage: import_market_prices.py "<path to Markte History.xlsx>"\n'
              "Reads the farm's market history workbook (its Sales sheet) - the farm's\n"
              "own file, not shipped with the app.")
        return
    xlsx_path = sys.argv[1]
    if not os.path.exists(xlsx_path):
        print(f"No such file: {xlsx_path}")
        return
    daily, totals, problems = load_rows(xlsx_path)
    if not totals:
        print("\n".join(problems or [f"No sales rows in {xlsx_path}"]) + "\nNothing imported.")
        return
    counts = {s: (len(daily.get(s, [])), len(rows)) for s, rows in totals.items()}   # before the commit expires them
    run_migrations()
    with Session(engine) as session:
        for season in totals:
            session.exec(delete(MarketPackPrice).where(MarketPackPrice.season_year == season))
            session.exec(delete(MarketDailySale).where(MarketDailySale.season_year == season))
            session.add_all(totals[season])
            session.add_all(daily.get(season, []))
        session.commit()
    print(f"Imported from {xlsx_path}:")
    for season in sorted(counts):
        print(f"  {season}: {counts[season][0]} daily sales, {counts[season][1]} season-total lines")
    for line in problems:
        print(f"  {line}")


if __name__ == "__main__":
    main()
