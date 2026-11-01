#!/usr/bin/env python3
"""Builds the farm's one clean market history workbook, "Markte History",
from the office's per-season "LIETJIES Markte <year> SEISOEN" workbooks -
a one-off, kept so the history can be rebuilt (another old season turns up,
a workbook gets corrected) and so the record of how it was read stays with
the code. scripts/import_market_prices.py then imports the clean workbook;
it never reads the old ones.

Why a clean workbook rather than importing the old ones directly: no two
seasons were kept the same way, and each had its own quirks (below). Read
once, here, into one sheet with one row per delivery note and pack type,
the history is something the office can look over, correct by hand, and
check a figure in - and the importer that reads it stays short.

Seasons after 2025 need none of this: the season being sold comes live
from Boord Packhouse's delivery notes and Boord's Markets & Transport
records (routers/market.py), which replaced the workbook.

What it reads in each old workbook:

  * The per-receiver sheets (ToroFruit, Dapper, AgriLink USA Lug...): a row
    per delivery, a five-column group per pack type ending PRICE. Their
    layouts differ by season - see _layout. 2022's "Local" sheet holds
    every local market, so each row's MARK is its receiver (MIXED_SHEETS).
  * Not the "<year> Totaal" sheets. The receiver sheets add up to them
    exactly (2024, and 2025 but for AgriLink's sea loads, which its Totaal
    leaves blank), so the season totals are worked out from the clean
    sheet's rows instead, and a Totaal copied into the next season's
    workbook - the 2024 workbook's "2023 Totaal" has a row of formula junk
    and 2023's 4.5kg air sales under "2kg MIX" - can't creep in.
  * A workbook's season is the year in its file name.

Usage (with the backend's venv, for openpyxl):
    backend/.venv/bin/python3 scripts/convert_market_workbooks.py "<out>.xlsx" <workbook.xlsx> [<workbook.xlsx> ...]
"""
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime

import openpyxl
from openpyxl.styles import Font, PatternFill

_SHEET_RE = re.compile(r"^(\d{4})\s+totaal$", re.IGNORECASE)
_FILE_YEAR_RE = re.compile(r"(20\d\d)")

# The clean workbook's Sales sheet, in column order. import_market_prices.py
# finds its columns by these names, so a column the office adds or moves
# doesn't break it; keep the two in step.
SALES_COLUMNS = ["Season", "Date", "Receiver", "Delivery note", "Pack type",
                 "Cartons sold", "R per carton", "Gross (R)", "From", "Note"]

# Pack-type names the workbooks and Boord Packhouse's own Settings ->
# Consignment list never agreed on at all - an Afrikaans word ("Kratte" is
# "crate"), the farm's own nickname for a size ("Rhino" for the 4.5kg XL
# carton), or the same carton written as 4.55kg in 2022. routers/market.py's
# _pack_key() already bridges case and punctuation when matching against
# Packhouse's live list, but it can only ever match the same word
# differently spelled - it has no way to know these are the same pack type
# at all. Matched ignoring capitals ("4.5Kg XL" in 2023). Same shape as
# block_renames.py: a record of this one farm's own naming, not a general
# mechanism. Add to this map rather than editing the old workbooks, which
# are the farm's own record and should keep saying what they said when they
# were written.
PACK_RENAMES = {
    "15 Kg Kratte": "15kg Crate",
    "15kg Krat": "15kg Crate",     # 2023, TFFG
    "Kratte": "15kg Crate",        # 2023, Tomahawk - the office's own 2023 total filed it as 15 Kg Kratte
    "4.5kg XL": "4.5kg Rhino",
    "4.55kg XL": "4.5kg Rhino",    # 2022
    "4.55kg XXL": "4.5kg XXL",     # 2022
    "PrePac L": "PrePac",          # 2022: the old layout gave every pack a grade, PrePac too
}

# Per-receiver sheet -> the receiver Boord Packhouse has for it (Settings ->
# Delivery Note), so a history season and a live season chart under the
# same names. "Lug" is air freight and "See" sea; the office kept AgriLink's
# 2kg air boxes on a sheet of their own, but they are the same receiver, so
# a day on both becomes one point, its price weighted by cartons
# (routers/market.py _daily). AgriLink's sea sheets are USA in every
# season (2024's and 2025's rows say so; 2022 and 2023 went on the same LW
# export notes), whatever each season called the sheet. AgriLink's other
# air sheet keeps one name of its own: 2024's went to NL/AE, not the USA.
# Only names that differ by more than case or punctuation need an entry:
# routers/market.py matches the rest against Packhouse's live receiver
# list. Also applied to each row's MARK on a MIXED_SHEETS sheet.
RECEIVER_RENAMES = {
    "AgriLink 2kg Lug": "Agrilink USA Air",
    "AgriLink USA Lug": "Agrilink USA Air",
    "AgriLink Lug USA": "Agrilink USA Air",   # 2024
    "AgriLink USA See": "Agrilink USA Sea",
    "AgriLink See": "Agrilink USA Sea",       # 2022, 2024
    "Agrilink SV 23": "Agrilink USA Sea",     # 2023
    "Agrilink Lug": "AgriLink Lug",           # 2023's spelling of 2022's and 2024's sheet
    "BoereMT": "Boere Mark Trust",
    "BoereTrust": "Boere Mark Trust",         # 2023
    "Boeretrust": "Boere Mark Trust",         # 2024, and 2022's MARK
    "TFFG See": "TFFGSA",
    "Toro Fruit": "ToroFruit",                # 2022, 2023
}

# Sheets that hold several receivers rather than one: 2022's "Local" kept
# every local market (Dapper, Boeretrust, TFFG...) on one sheet, a row each,
# so each row's receiver is its own MARK column, not the sheet name.
MIXED_SHEETS = {"Local"}

# Per season, sheets that copy deliveries another sheet in the same
# workbook already has (the office's habit of duplicating a sheet before
# reworking it), which would count the same sales twice. Each was compared
# against the others delivery note by delivery note.
#   2022: "AgriLink Lug" (air) and "AgriLink See" (sea) are the season's
#         statements. "AgriLink" is both put together, with the sea notes
#         at the R87.63 advance payment instead of the sea sheet's price;
#         "AgriLink (3)" and "AgriLink (5)" are part-copies of it.
#   2023: "Agrilink LU 23" is a part-copy of "Agrilink Lug".
SKIP_SHEETS = {
    2022: {"AgriLink", "AgriLink (3)", "AgriLink (5)"},
    2023: {"Agrilink LU 23"},
}

# What the office should know about a whole sheet's prices, written into
# each of its rows' Note. AgriLink's sea loads are never paid in full by
# the time the workbook is closed, and the sheet doesn't say which price it
# holds. 2022's is settled: its sea sheet's R336-R360 a carton, the higher
# of the two figures the workbook has, is the one to use (the owner's call),
# not the R87.63 advance on its payments sheet - which is also why the
# combined "AgriLink" sheet is in SKIP_SHEETS.
SHEET_NOTES = {
    (2025, "AgriLink USA See"): "Sea: R78-R84, likely the advance; the 2025 Totaal leaves sea blank. Final?",
}

# Datum typos worked out from the delivery notes either side, by season and
# delivery note: the notes are numbered in the order they were written, so a
# note between two dated ones went out between their dates, and each typo
# kept enough of its day to place it.
#   2023 note 922 (Dapper, "26/09/202"): 920 went on 20 Sep, 923 on 26 Sep.
#   2023 note 965 (BoereTrust, "20/14//2023"): 964 went on 20 Nov, 966 on 21 Nov.
DATE_FIXES = {
    (2023, "922"): (date(2023, 9, 26), "notes 920 and 923"),
    (2023, "965"): (date(2023, 11, 20), "notes 964 and 966"),
}

_PACK_RENAMES = {k.lower(): v for k, v in PACK_RENAMES.items()}


def _clean(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _pack_type(label_cell, season_year: int) -> str:
    """The group label, with a leading "<season_year> " stripped - one
    season's sheet (2023) wrote its pack-type headers as "2023 2kg XL"
    rather than "2kg XL" - and passed through PACK_RENAMES."""
    label = _clean(label_cell)
    prefix = f"{season_year} "
    if label.startswith(prefix):
        label = label[len(prefix):]
    return _PACK_RENAMES.get(label.lower(), label)


def _sale_date(cell):
    """The Datum column. Each season's office typed it its own way: as text,
    "07.11.25" (2025, 2024) or "13.10.2022" (2022, part of 2023), or as a
    real date cell (most of 2023) - all taken. Anything else is None, never a
    guess: a sale filed under the wrong day would be worse than a missing
    one."""
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    text = _clean(cell)
    for fmt in ("%d.%m.%y", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _layout(ws):
    """Where a per-receiver sheet keeps its figures: (header row index,
    [(first column, pack type label), ...], {column name: index}), or None
    for a sheet with no PRICE header at all (a bank-payment list, a picking
    record, a summary of cartons only - nothing to chart).

    The header row is found by its PRICE cells within the first few rows,
    because the sheets don't all start on the same row ("Andre" in 2025, the
    2022 export receivers a row lower than the rest). A pack-type group is
    the columns up to its PRICE - SUB after it is not required, since some
    2023 sheets have it on the row above instead - and its label is read
    two ways:
      * 2023 onward, "SEND, SOLD, OUTSTANDING, PRICE, SUB" under a label row
        naming the pack type ("2kg XXL");
      * 2022, the group's first column holds the grade instead of "SEND"
        ("XXL, Sold, Outstandin, PRICE, SUB") under a row with only the
        carton size ("4.55kg"), so the pack type is the two put together.
    The column names come from both rows, since some 2023 sheets put
    "Datum, MARK, NR" on the label row rather than the header row."""
    top = [[c.value for c in r] for r in ws.iter_rows(min_row=1, max_row=6)]
    hdr = next((i for i, r in enumerate(top) if any(_clean(v).upper() == "PRICE" for v in r)), None)
    if not hdr:
        return None
    head, label = top[hdr], top[hdr - 1]
    cell = lambda row, i: _clean(row[i]) if 0 <= i < len(row) else ""
    groups = []
    for p in range(3, len(head)):
        if cell(head, p).upper() != "PRICE" or not cell(head, p - 2).upper().startswith("SOLD"):
            continue
        start = p - 3
        first = cell(head, start)
        groups.append((start, cell(label, start) if first.upper() == "SEND"
                       else f"{cell(label, start)} {first}".strip()))
    cols = {}
    for row in (label, head):
        for i, v in enumerate(row):
            cols.setdefault(_clean(v).upper(), i)
    return hdr, groups, cols


def sheet_rows(ws, season: int) -> list:
    """A per-receiver sheet's priced lines as dicts, one per pack type per
    row: season, date, receiver, note_no, pack_type, cartons, price, sheet,
    source (for the From column), note. The receiver is the sheet's own name (RECEIVER_RENAMES), or each
    row's MARK for a sheet that holds several (MIXED_SHEETS).

    A row with no Datum is one of two things. Where the office put a second
    line on the same delivery note (two cultivars, two pallets) it left the
    date out, so the line belongs to the row above it. Where a row adds up
    the sheet - its cartons equal everything above it, pack type by pack
    type - it is the totals row, and is passed over; so is any blank-dated
    row before the first delivery (the 2024 sheets' summary line at the
    top). A row whose Datum is a typo rather than a date ("26/09/202") takes
    its date from DATE_FIXES, its From column saying so; one not there gets
    no date and a Note saying what was typed, for the office to fill in."""
    layout = _layout(ws)
    if not layout:
        return []
    hdr, groups, cols = layout
    groups = [(start, _pack_type(label, season)) for start, label in groups]
    title = _clean(ws.title)
    sheet_receiver = RECEIVER_RENAMES.get(title, title)
    mark_col = cols.get("MARK", 1)
    note_col = cols.get("NR", cols.get("VRAG NR", 3))
    cell = lambda row, i: _clean(row[i]) if i < len(row) else ""
    running = defaultdict(float)   # pack type -> cartons sold so far on this sheet
    out, last = [], None           # last: (date or None, receiver, note_no, typed Datum) of the delivery above
    for row in ws.iter_rows(min_row=hdr + 2, values_only=True):
        lines = []
        for start, pack_type in groups:
            sold = row[start + 1] if start + 1 < len(row) else None
            price = row[start + 3] if start + 3 < len(row) else None
            if not pack_type or not isinstance(sold, (int, float)) or not sold:
                continue
            if not isinstance(price, (int, float)) or not price:
                continue  # '#DIV/0!' (a formula error) or genuinely 0 - neither is a price
            lines.append((pack_type, float(sold), float(price)))
        if cell(row, 0):
            who = sheet_receiver
            if title in MIXED_SHEETS:
                who = RECEIVER_RENAMES.get(cell(row, mark_col), cell(row, mark_col))
            last = (_sale_date(row[0]), who, cell(row, note_col), cell(row, 0))
        elif not lines or last is None or all(abs(sold - running[pack]) < 0.5 for pack, sold, _ in lines):
            continue   # nothing priced, before the first delivery, or the totals row
        for pack, sold, _ in lines:
            running[pack] += sold
        sale_date, who, note_no, typed = last
        notes = [SHEET_NOTES[(season, title)]] if (season, title) in SHEET_NOTES else []
        source = f"{season} workbook, {title}"
        if sale_date is None and (season, note_no) in DATE_FIXES:
            sale_date, between = DATE_FIXES[(season, note_no)]
            source += f"; Datum typed {typed!r}, date from {between}"
        elif sale_date is None:
            notes.insert(0, f"Datum typed as {typed!r}: fill in the date.")
        out += [{"season": season, "date": sale_date, "receiver": who, "note_no": note_no,
                 "pack_type": pack, "cartons": sold, "price": price, "sheet": title, "source": source,
                 "note": " ".join(notes)}
                for pack, sold, price in lines]
    return out


def workbook_rows(path: str) -> list:
    """Every priced line in one old workbook, its season from the file
    name; its Totaal sheets and SKIP_SHEETS left out."""
    m = _FILE_YEAR_RE.search(os.path.basename(path))
    if not m:
        raise SystemExit(f"No year in the file name of {path} - rename it 'LIETJIES Markte <year> ...'.")
    season = int(m.group(1))
    wb = openpyxl.load_workbook(path, data_only=True)
    skip = SKIP_SHEETS.get(season, set())
    rows = []
    for ws in wb.worksheets:
        if not _SHEET_RE.match(ws.title.strip()) and _clean(ws.title) not in skip:
            rows += sheet_rows(ws, season)
    return rows


_HEAD = Font(bold=True, color="FFFFFF")
_HEAD_FILL = PatternFill("solid", fgColor="1F3864")
_NOTE_FILL = PatternFill("solid", fgColor="FFF2CC")


def _header(ws, cells: list) -> None:
    ws.append(cells)
    for c in ws[ws.max_row]:
        c.font, c.fill = _HEAD, _HEAD_FILL


def write_history(rows: list, out_path: str) -> None:
    """The clean workbook: Sales (one row per delivery note and pack type,
    the sheet import_market_prices.py reads), Check (every row with a
    Note), and Read me."""
    rows = sorted(rows, key=lambda r: (r["season"], r["date"] or date.max, r["receiver"], r["note_no"], r["pack_type"]))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    _header(ws, SALES_COLUMNS)
    for r in rows:
        ws.append([r["season"], r["date"], r["receiver"], r["note_no"], r["pack_type"], r["cartons"], r["price"],
                   None, r["source"], r["note"] or None])
        n = ws.max_row
        ws.cell(row=n, column=8).value = f"=F{n}*G{n}"
        ws.cell(row=n, column=2).number_format = "dd/mm/yyyy"
        ws.cell(row=n, column=7).number_format = "#,##0.00"
        ws.cell(row=n, column=8).number_format = "#,##0.00"
        if r["note"]:
            ws.cell(row=n, column=10).fill = _NOTE_FILL
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:J{ws.max_row}"
    for col, width in zip("ABCDEFGHIJ", (8, 12, 20, 13, 16, 12, 12, 14, 30, 70)):
        ws.column_dimensions[col].width = width

    check = wb.create_sheet("Check")
    check.append(["Rows to look at. Correct them on the Sales sheet, then clear the Note."])
    check["A1"].font = Font(bold=True)
    check.append([])
    _header(check, ["Sales row", "Season", "Date", "Receiver", "Delivery note", "Pack type", "Note"])
    for i, r in enumerate(rows, start=2):
        if r["note"]:
            check.append([i, r["season"], r["date"], r["receiver"], r["note_no"], r["pack_type"], r["note"]])
            check.cell(row=check.max_row, column=3).number_format = "dd/mm/yyyy"
    for col, width in zip("ABCDEFG", (10, 8, 12, 20, 13, 16, 90)):
        check.column_dimensions[col].width = width

    seasons = sorted({r["season"] for r in rows})
    readme = wb.create_sheet("Read me")
    for line in [
        f"Litchi market history, {seasons[0]} to {seasons[-1]}" if seasons else "Litchi market history",
        "",
        "The farm's market sales before Boord kept them, for Boord Owner's Markets tab.",
        "Built once from the office's 'LIETJIES Markte <year> SEISOEN' workbooks; this is now the copy to correct.",
        "",
        "Sales: one row per delivery note and pack type - what that receiver sold and the price per carton.",
        "  Owner reads Season, Date, Receiver, Delivery note, Pack type, Cartons sold and R per carton.",
        "  Gross is worked out; From says where the row came from; Note is for anything to check.",
        "  The season totals on the Markets tab are added up from these rows, weighted by cartons.",
        "  A row with no date counts in its season's total, but is not on the daily chart.",
        "Check: the rows with a Note. Fix them on Sales, clear the Note, and import the file again.",
        "",
        "Do not add seasons after 2025 here. Owner reads those live from Boord Packhouse's delivery notes",
        "and Boord's market invoices and statements; a season in this file would replace them.",
        "",
        "To load it into Owner, on the farm PC in C:\\BoordOwner:",
        '  backend\\.venv\\Scripts\\python.exe scripts\\import_market_prices.py "data\\imports\\Markte History.xlsx"',
    ]:
        readme.append([line])
    readme["A1"].font = Font(bold=True, size=14)
    readme.column_dimensions["A"].width = 110
    wb.save(out_path)


def main():
    if len(sys.argv) < 3:
        print('Usage: convert_market_workbooks.py "<out>.xlsx" <workbook.xlsx> [<workbook.xlsx> ...]\n'
              "Builds the one clean market history workbook from the office's per-season\n"
              "'LIETJIES Markte <year> SEISOEN' workbooks.")
        return
    out_path, sources = sys.argv[1], sys.argv[2:]
    rows = []
    for path in sources:
        got = workbook_rows(path)
        print(f"  {os.path.basename(path)}: {len(got)} sales lines")
        rows += got
    write_history(rows, out_path)
    notes = sum(1 for r in rows if r["note"])
    print(f"Wrote {len(rows)} sales lines to {out_path}, {notes} with a Note to check.")


if __name__ == "__main__":
    main()
