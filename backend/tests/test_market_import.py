"""The market history: scripts/convert_market_workbooks.py, which reads the
office's per-season "LIETJIES Markte" workbooks (2022 to 2025) once into one
clean "Markte History" workbook, and scripts/import_market_prices.py, which
imports that clean workbook into MarketDailySale and MarketPackPrice.

The farm's own workbooks are per-farm data and never in the repo, so each
converter test builds a small workbook in the layout of the season it
stands for: 2024/2025 (SEND/SOLD/... receiver sheets beside a Totaal sheet),
2023 (dates as date cells, a SUB header on the wrong row, a copied sheet)
and 2022 (the grade where SEND would be, one "Local" sheet for every local
market)."""
import os
import sys
from datetime import date, datetime

import openpyxl
from sqlmodel import Session, select

from db import owner_engine
from models_owner import MarketDailySale, MarketPackPrice

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import convert_market_workbooks as conv  # noqa: E402
import import_market_prices as imp  # noqa: E402

GROUP = ["SEND", "SOLD", "OUTSTANDING", "PRICE", "SUB"]


def _sheet(wb, title, label, head, rows):
    """A sheet: an empty first row (as the office's have), the label row,
    the header row, then the data rows."""
    ws = wb.create_sheet(title)
    ws.append([])
    ws.append(label)
    ws.append(head)
    for r in rows:
        ws.append(r)
    return ws


def _receiver_2025(wb, title, rows):
    """A 2024/2025 per-receiver sheet: Datum, MARK, DESTIN, NR, then two
    pack-type groups, 2kg XXL and 2kg XL. A Totaal sheet has the same
    shape, with the buyer in column B."""
    label = [None] * 4 + ["2kg XXL", None, None, None, None, "2kg XL", None, None, None, None]
    return _sheet(wb, title, label, ["Datum", "MARK", "DESTIN", "NR"] + GROUP + GROUP, rows)


def _save(wb, tmp_path, name):
    del wb["Sheet"]
    path = str(tmp_path / name)
    wb.save(path)
    return path


def _workbook_2025(tmp_path):
    wb = openpyxl.Workbook()
    _receiver_2025(wb, "Dapper", [
        [None, "Dapper", None, None, 30, 30, 0, 200, 6000, 0, 0, 0, None, 0],   # summary line above the first delivery
        ["07.11.25", "Dapper", "LC", 1201, 10, 10, 0, 200, 2000, 0, 0, 0, "#DIV/0!", 0],
        [None, None, None, None, 20, 20, 0, 200, 4000, 0, 0, 0, "#DIV/0!", 0],    # a second line on note 1201
        ["10.11.25", "Dapper", "LC", 1205, 0, 0, 0, "#DIV/0!", 0, 5, 5, 0, 150, 750],
        [None, None, None, None, 30, 30, 0, 200, 6000, 5, 5, 0, 150, 750],        # the totals row
    ])
    _receiver_2025(wb, "AgriLink USA Lug", [
        ["08.11.25", "AgriLink", "USA", "LW3001", 100, 100, 0, 300, 30000, 0, 0, 0, "#DIV/0!", 0],
    ])
    _receiver_2025(wb, "2025 Totaal", [   # never read: the receiver sheets are the record
        [None, "Dapper", None, None, 999, 999, 0, 1, 999, 0, 0, 0, "#DIV/0!", 0],
    ])
    return _save(wb, tmp_path, "LIETJIES Markte 2025 SEISOEN.xlsx")


def test_a_second_line_joins_the_delivery_above_and_the_totals_row_is_skipped(tmp_path):
    """Note 1201's undated second line is a sale on 7 November; the summary
    line above the first delivery, the totals row at the bottom and the
    Totaal sheet are none of them sales, or Dapper's 2kg XXL would be
    counted several times over."""
    rows = conv.workbook_rows(_workbook_2025(tmp_path))
    assert sorted((r["receiver"], r["date"], r["note_no"], r["pack_type"], r["cartons"]) for r in rows) == [
        ("Agrilink USA Air", date(2025, 11, 8), "LW3001", "2kg XXL", 100),   # RECEIVER_RENAMES
        ("Dapper", date(2025, 11, 7), "1201", "2kg XXL", 10),
        ("Dapper", date(2025, 11, 7), "1201", "2kg XXL", 20),
        ("Dapper", date(2025, 11, 10), "1205", "2kg XL", 5),
    ]
    assert {r["season"] for r in rows} == {2025}   # from the file name


def _workbook_2023(tmp_path):
    """2023: real date cells and a text one, a typo date, the 2kg XL group's
    SUB header on the label row instead of the header row, an Afrikaans
    crate name, and a sheet that copies another."""
    wb = openpyxl.Workbook()
    label = ["Datum", "MARK", "DESTIN", "NR", "2kg XXL", None, None, None, None, "2kg XL", None, None, None, "SUB"]
    head = [None, None, None, None] + GROUP + ["SEND", "SOLD", "OUTSTANDING", "PRICE", None]
    rows = [
        [datetime(2023, 9, 20), "Agrilink", "LU", 919, 65, 65, 0, 345, 22425, 48, 47, 1, 345, 16215],
        ["13.09.2023", "Agrilinl", "LU", 917, 10, 10, 0, 300, 3000, 0, 0, 0, "#DIV/0!", 0],
        ["26/09/202", "Agrilink", "LU", 922, 5, 5, 0, 310, 1550, 0, 0, 0, "#DIV/0!", 0],
    ]
    _sheet(wb, "Agrilink Lug", label, head, rows)
    _sheet(wb, "Agrilink LU 23", label, head, rows[:1])   # a part-copy (SKIP_SHEETS)
    _sheet(wb, "TFFG", [None] * 4 + ["15kg Krat ", None, None, None, None], ["Datum", "MARK", "DESTIN", "NR"] + GROUP,
           [[datetime(2023, 11, 21), "NuLeaf Brands (TFFG)", "LC", 966, 45, 45, 0, 975, 43875]])
    return _save(wb, tmp_path, "LIETJIES Markte 2023 SEISOEN.xlsx")


def test_the_2023_layout_and_its_typo_date(tmp_path):
    rows = sorted(conv.workbook_rows(_workbook_2023(tmp_path)), key=lambda r: (r["date"] or date.max, r["pack_type"]))
    assert [(r["receiver"], r["date"], r["pack_type"], r["cartons"]) for r in rows] == [
        ("AgriLink Lug", date(2023, 9, 13), "2kg XXL", 10),
        ("AgriLink Lug", date(2023, 9, 20), "2kg XL", 47),    # read without its SUB header
        ("AgriLink Lug", date(2023, 9, 20), "2kg XXL", 65),
        ("TFFG", date(2023, 11, 21), "15kg Crate", 45),       # PACK_RENAMES
        ("AgriLink Lug", None, "2kg XXL", 5),                 # note 922: kept, its date left for the office
    ]   # and nothing from "Agrilink LU 23", the copy
    assert rows[-1]["note"] == "Datum typed as '26/09/202': fill in the date."


def _workbook_2022(tmp_path):
    """2022's older layout: the header a row lower on some sheets, the
    group's first column the grade rather than SEND, the size alone on the
    label row - and every local market on one "Local" sheet."""
    wb = openpyxl.Workbook()
    label = [None] * 5 + ["4.55kg", None, None, None, None, "PrePac", None, None, None, None]
    head = [None, "MARK", "NR", "WEEK", "KULTIVAR", "XL", "Sold", "Outstandin", "PRICE", "SUB",
            "L", "Sold", "Outstandin", "PRICE", "SUB"]
    ws = _sheet(wb, "AgriLink See", label, head, [
        ["17.11.2022", "AgriLink", "LW2224", 46, "MAU", 4100, 4100, 0, 338, 1385800, 0, 0, 0, "#DIV/0!", 0],
        ["13.10.2022", "AgriLink", "LW2188", 41, "TMR", 0, 0, 0, "#DIV/0!", 0, 384, 384, 0, 284, 109056],
    ])
    ws.insert_rows(1)   # this one starts a row lower
    _sheet(wb, "AgriLink", label, head, [   # the combined copy (SKIP_SHEETS)
        ["17.11.2022", "AgriLink", "LW2224", 46, "MAU", 4100, 4100, 0, 87.65, 359365, 0, 0, 0, "#DIV/0!", 0],
    ])
    _sheet(wb, "Local", [None] * 6 + ["PrePac", None, None, None, None],
           [None, "MARK", "DESTIN", "NR", "WEEK", "KULTIVAR"] + GROUP, [
               ["22.09.2022", "Dapper", "LC", 801, 38, "TMR", 3, 3, 0, 320, 960],
               ["12.10.2022", "Boeretrust", "LC", 812, 41, "TMR", 20, 20, 0, 200, 4000],
           ])
    return _save(wb, tmp_path, "LIETJIES Markte 2022 SEISOEN.xlsx")


def test_the_2022_layout_puts_size_and_grade_together(tmp_path):
    rows = conv.workbook_rows(_workbook_2022(tmp_path))
    assert sorted((r["receiver"], r["note_no"], r["pack_type"], r["cartons"], r["price"]) for r in rows) == [
        ("Agrilink USA Sea", "LW2188", "PrePac", 384, 284),        # "PrePac L" -> "PrePac"
        ("Agrilink USA Sea", "LW2224", "4.5kg Rhino", 4100, 338),  # "4.55kg XL" -> "4.5kg Rhino"
        ("Boere Mark Trust", "812", "PrePac", 20, 200),           # Local: the receiver is the row's MARK
        ("Dapper", "801", "PrePac", 3, 320),
    ]   # and nothing from "AgriLink", the combined copy at the advance-payment price
    assert all(r["note"].startswith("Sea:") for r in rows if r["receiver"] == "Agrilink USA Sea")   # SHEET_NOTES


def _history(tmp_path, *sources):
    out = str(tmp_path / "Markte History.xlsx")
    rows = []
    for s in sources:
        rows += conv.workbook_rows(s)
    conv.write_history(rows, out)
    return out


def test_the_clean_workbook_round_trips_through_the_importer(client, tmp_path, monkeypatch, capsys):
    """Convert 2022 and 2023, import the result: the daily chart gets every
    dated line, the season totals every line (note 922 too), weighted by
    cartons, and the import says which row had no date."""
    path = _history(tmp_path, _workbook_2022(tmp_path), _workbook_2023(tmp_path))
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames == ["Sales", "Check", "Read me"]
    assert [c.value for c in wb["Sales"][1]] == conv.SALES_COLUMNS
    monkeypatch.setattr(sys, "argv", ["import_market_prices.py", path])
    imp.main()
    with Session(owner_engine) as owner:
        daily = owner.exec(select(MarketDailySale)).all()
        totals = {(t.season_year, t.buyer, t.pack_type): t for t in owner.exec(select(MarketPackPrice)).all()}
    assert len(daily) == 8 and not any(d.note_no == "922" for d in daily)
    xxl = totals[(2023, "AgriLink Lug", "2kg XXL")]
    assert (xxl.cartons_sold, xxl.sub_total) == (80, 22425 + 3000 + 1550)   # 65 @ 345, 10 @ 300, 5 @ 310
    assert xxl.price_per_carton == round((22425 + 3000 + 1550) / 80, 2)
    r = client.get("/api/market/summary").json()
    assert r["seasons"] == [2022, 2023] and r["daily"]["workbook_seasons"] == [2022, 2023]
    # PrePac 2022: 384 @ 284 + 3 @ 320 + 20 @ 200
    assert r["by_season"]["2022"]["PrePac"] == {"cartons": 407.0,
                                                "rand_per_carton": round((109056 + 960 + 4000) / 407, 2)}
    assert "isn't a date" not in capsys.readouterr().out   # a blank date is expected, not a problem


def test_an_office_correction_on_the_sales_sheet_is_what_imports(client, tmp_path, monkeypatch, capsys):
    """The clean workbook is the copy the office corrects: a date filled in
    by hand puts the row on the chart, a typo'd one is reported, and a row
    with no price is left out and named - and a re-import replaces only the
    seasons the file has."""
    with Session(owner_engine) as owner:
        owner.add(MarketPackPrice(season_year=2026, buyer="Dapper", pack_type="PrePac", cartons_sold=1,
                                  price_per_carton=1, sub_total=1))
        owner.commit()
    path = _history(tmp_path, _workbook_2023(tmp_path))
    wb = openpyxl.load_workbook(path)
    ws = wb["Sales"]
    heads = [c.value for c in ws[1]]
    date_col, price_col = heads.index("Date") + 1, heads.index("R per carton") + 1
    undated = next(r for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=date_col).value is None)
    ws.cell(row=undated, column=date_col).value = "26/09/2023"
    ws.cell(row=2, column=date_col).value = "31/02/2023"
    ws.cell(row=3, column=price_col).value = None
    wb.save(path)
    monkeypatch.setattr(sys, "argv", ["import_market_prices.py", path])
    imp.main()
    out = capsys.readouterr().out
    assert "Sales row 2: date '31/02/2023' isn't a date" in out
    assert "Sales row 3: needs a season, receiver, pack type, cartons and price - left out." in out
    with Session(owner_engine) as owner:
        days = {d.sale_date for d in owner.exec(select(MarketDailySale)).all()}
        seasons = {t.season_year for t in owner.exec(select(MarketPackPrice)).all()}
    assert date(2023, 9, 26) in days and seasons == {2023, 2026}


def test_a_file_without_a_sales_sheet_imports_nothing(client, tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "Markte.xlsx")
    openpyxl.Workbook().save(path)
    monkeypatch.setattr(sys, "argv", ["import_market_prices.py", path])
    imp.main()
    assert "No 'Sales' sheet" in capsys.readouterr().out
    with Session(owner_engine) as owner:
        assert owner.exec(select(MarketDailySale)).all() == []
