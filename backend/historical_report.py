"""The Historical Harvest Data report, lifted out of Boord's
backend/routers/reports.py verbatim.

Every harvest figure the farm had on file, 1987 to the current season, in
one workbook. It was the last screen standing on HistoricalHarvest and
HistoricalAnnualYield, and it left Boord with them.

This will not run as it stands. It is the body of one endpoint, and it
needs, from what used to surround it in reports.py: the `router`, `REPORTS_DIR`,
`XLSX_MEDIA`, `_style_header_cell()`, `_block_sort_key()` (which lived in
analysis.py - see analysis.py here), the openpyxl imports, and the models
HarvestRecord, HistoricalHarvest, HistoricalAnnualYield, Block.
"""
@router.get("/historical-harvest-data")
def historical_harvest_data_report(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Historical Harvest Data: every harvest figure this farm has on file,
    1987 through the current season, in one workbook. Not date-range
    filtered like the other reports - there's only ever one of these.

    Three different grains of record sit side by side here, which is why
    there are several sheets rather than one big grid:
      - Per-year sheets (2020 on): the full block x date pivot - the farm's
        own "Daaglikse Oesdata" workbook, kept live. Pre-app seasons come
        from HistoricalHarvest (imported once - see
        scripts/import_historical_harvest.py for provenance and the
        block-split-by-hectare-ratio caveat); the current season is built
        fresh from HarvestRecord on every download, so it's always up to
        date without a re-import.
      - Annual Totals (1987-2019): season totals only, no daily breakdown -
        per block back to 2012, whole-farm-only before that (see
        scripts/import_historical_annual_yield.py).
      - Season Summary and Block by Year: cross-era views built from
        whichever of the above covers each season, so the whole record can
        be read at once. Season Summary names each season's grain in its
        own column so the two are never silently mixed."""
    settings = session.exec(select(SystemSetting)).first()
    current_year = settings.current_harvest_year if settings else date.today().year
    blocks = {b.id: b for b in session.exec(select(Block)).all()}

    day_kg: dict = {}  # (year, block_id, date) -> kg
    estimated_blocks: set = set()
    for h in session.exec(select(HistoricalHarvest)).all():
        key = (h.season_year, h.block_id, h.harvest_date)
        day_kg[key] = day_kg.get(key, 0.0) + h.kg
        if h.estimated:
            estimated_blocks.add(h.block_id)
    for r in session.exec(select(HarvestRecord)).all():
        local_ts = to_local(r.timestamp)
        if local_ts is None or local_ts.year != current_year:
            continue
        key = (current_year, r.block_id, local_ts.date())
        day_kg[key] = day_kg.get(key, 0.0) + (r.weight_kg - r.deduction_kg)

    years = sorted({k[0] for k in day_kg})

    annual_kg: dict = {}  # (year, block_id) -> kg
    annual_estimated_blocks: set = set()
    for a in session.exec(select(HistoricalAnnualYield)).all():
        key = (a.season_year, a.block_id)
        annual_kg[key] = annual_kg.get(key, 0.0) + a.kg
        if a.estimated:
            annual_estimated_blocks.add(a.block_id)
    annual_years = sorted({k[0] for k in annual_kg})

    def block_label(bid, estimated_set=estimated_blocks):
        b = blocks.get(bid)
        name = b.name if b else (bid or "")
        return f"{name}*" if bid in estimated_set else name

    # One per-block annual figure per (year, block), however that year was
    # recorded: summed from the daily sheets where those exist (2020 on),
    # taken straight from the annual-only import before that. The 1987-2009
    # rows carry block_id None - a whole-farm total with no block breakdown
    # (see HistoricalAnnualYield) - so they're deliberately excluded here
    # and appear only in the Season Summary's total.
    block_year_kg: dict = {}  # (year, block_id) -> kg
    for (year, block_id, _), kg in day_kg.items():
        block_year_kg[(year, block_id)] = block_year_kg.get((year, block_id), 0.0) + kg
    for (year, block_id), kg in annual_kg.items():
        if block_id is not None:
            block_year_kg[(year, block_id)] = block_year_kg.get((year, block_id), 0.0) + kg

    all_years = sorted({y for y, _ in block_year_kg} | set(annual_years) | set(years))
    all_estimated = estimated_blocks | annual_estimated_blocks

    # How each season was recorded, so a reader can tell a real daily record
    # from a single hand-written season total - they are not equally solid.
    annual_per_block_years = {y for (y, bid) in annual_kg if bid is not None}

    def granularity(year):
        if year in years:
            return "Daily, per block" if year != current_year else "Daily, per block (in progress)"
        # Keyed off the data itself rather than today's block register, so a
        # historical block that's since been removed from the register still
        # counts as a per-block season rather than silently reading as
        # whole-farm-only.
        if year in annual_per_block_years:
            return "Season total, per block"
        return "Season total, whole farm only"

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    # --- Blocks reference sheet ---
    bs = wb.create_sheet("Blocks")
    bs.append(["Id", "Name", "Variety", "Trees", "Hectares", "Active"])
    for c in bs[1]:
        _style_header_cell(c)
    for block_id in sorted(blocks, key=_block_sort_key):
        b = blocks[block_id]
        bs.append([b.id, b.name, b.variety, b.trees, b.hectares, "Active" if b.active else "Inactive"])
    bs.freeze_panes = "A2"
    bs.column_dimensions["B"].width = 16

    # --- Notes sheet ---
    ns = wb.create_sheet("Notes")
    ns.column_dimensions["A"].width = 100
    notes = [
        "Notes on this report",
        "",
        f"Generated {date.today().isoformat()}. Historical seasons ({years[0] if years else '-'}-"
        f"{current_year - 1}) are a fixed, one-time import from the farm's pre-app records. The current "
        f"season ({current_year}) sheet is generated fresh from live harvest data every time this report "
        "is downloaded.",
        "",
        "Block splits: a handful of today's blocks (8a/8b, 10a/10b, 17a/17b, 19a/19b) didn't exist "
        "separately before this app - the original records had one combined daily total for each pair. "
        "Those historical totals were split between the two sub-blocks in proportion to their hectares. "
        "This is an ESTIMATE, not an actually recorded per-sub-block figure - affected block names are "
        "marked with an asterisk (*) in each year's sheet.",
        "",
        "Figures are in kg per block per day.",
        "",
        "Season Summary lists every season on file with its total and how it was recorded - a real "
        "day-by-day record and a single hand-written season total are both here, and the "
        "\"How It Was Recorded\" column says which is which. Block by Year puts every block-level "
        "season total in one grid, whichever way that year was recorded, so blocks can be compared "
        "across the years side by side; seasons with no block breakdown at all (1987-2009) are "
        "counted in Season Summary but can't appear there.",
    ]
    if annual_years:
        notes += [
            "",
            f"Annual Totals sheet ({annual_years[0]}-{annual_years[-1]}): the farm's older records only "
            "kept totals per SEASON, not per day, so these years have no daily breakdown and aren't part "
            "of the Analysis tab or Risk indicator (which need day-by-day figures, and weather data only "
            "goes back to 2020 anyway). The same block-split estimate and asterisk convention above "
            "applies to the per-block years here too.",
            "",
            "Rows marked \"whole-farm total only\" in that sheet's Notes column go back further still "
            "(1987-2009) to records that predate today's block register entirely, under a completely "
            "different, incompatible block-numbering scheme. Rather than guess at a mapping between old "
            "and new block numbers, only each year's whole-farm total was kept for those rows - no "
            "per-block breakdown is available.",
        ]
    for i, line in enumerate(notes, start=1):
        cell = ns.cell(row=i, column=1, value=line)
        if i == 1:
            cell.font = Font(bold=True, size=14)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    # --- Season Summary: one row per season, every year on file ---
    if all_years:
        ss = wb.create_sheet("Season Summary")
        ss.append(["Year", "Total Kg", "Change vs Previous", "Blocks Recorded", "How It Was Recorded"])
        for c in ss[1]:
            _style_header_cell(c)
        season_total = {}
        for year in all_years:
            if year in years:
                season_total[year] = sum(kg for (y, _, _), kg in day_kg.items() if y == year)
            elif (year, None) in annual_kg:
                season_total[year] = annual_kg[(year, None)]
            else:
                season_total[year] = sum(kg for (y, bid), kg in annual_kg.items()
                                          if y == year and bid is not None)
        for year in all_years:
            total = season_total[year]
            block_count = len({bid for (y, bid) in block_year_kg if y == year and bid is not None})
            # Only against the season immediately before - the record has
            # gaps (nothing for 2010-2011), and calling a three-year jump a
            # year-on-year change would read as a collapse or a boom that
            # never happened.
            prev = season_total.get(year - 1)
            change = (total - prev) / prev if prev else None
            ss.append([year, round(total, 1), change, block_count or None, granularity(year)])
        for r in range(2, ss.max_row + 1):
            ss.cell(row=r, column=3).number_format = "+0.0%;-0.0%"
        ss.freeze_panes = "A2"
        for col, width in zip("ABCDE", (8, 14, 18, 16, 30)):
            ss.column_dimensions[col].width = width

    # --- Block by Year: every block-level season total in one grid ---
    if block_year_kg:
        by_years = sorted({y for y, _ in block_year_kg})
        by_blocks = sorted({bid for _, bid in block_year_kg if bid is not None}, key=_block_sort_key)
        bys = wb.create_sheet("Block by Year")
        bys.append(["Block", "Variety", "Trees", "Hectares"] + [str(y) for y in by_years] + ["Total"])
        for c in bys[1]:
            _style_header_cell(c)
        for bid in by_blocks:
            b = blocks.get(bid)
            row = [block_label(bid, all_estimated), b.variety if b else "",
                   b.trees if b else None, b.hectares if b else None]
            vals = [block_year_kg.get((y, bid)) for y in by_years]
            row += [round(v, 1) if v is not None else None for v in vals]
            row.append(round(sum(v for v in vals if v is not None), 1))
            bys.append(row)
        totals_row = ["TOTAL", "", None, None]
        for y in by_years:
            totals_row.append(round(sum(kg for (yy, bid), kg in block_year_kg.items()
                                         if yy == y and bid is not None), 1))
        totals_row.append(round(sum(kg for (_, bid), kg in block_year_kg.items() if bid is not None), 1))
        bys.append(totals_row)
        for c in bys[bys.max_row]:
            c.font = Font(bold=True)
        bys.freeze_panes = "E2"
        bys.column_dimensions["A"].width = 16
        bys.column_dimensions["B"].width = 12
        for col in range(3, len(by_years) + 6):
            bys.column_dimensions[get_column_letter(col)].width = 10

    # --- Annual Totals sheet (even older, season-only figures) ---
    if annual_years:
        # block_id None marks a whole-farm-only year (no block breakdown available that far back)
        annual_blocks = sorted({bid for (_, bid) in annual_kg if bid is not None}, key=_block_sort_key)
        farm_total_years = {y for (y, bid) in annual_kg if bid is None}
        as_ = wb.create_sheet("Annual Totals")
        header = (["Year"] + [block_label(bid, annual_estimated_blocks) for bid in annual_blocks] +
                   ["Total", "Notes"])
        as_.append(header)
        for c in as_[1]:
            _style_header_cell(c)
        for year in annual_years:
            if year in farm_total_years:
                total = annual_kg[(year, None)]
                as_.append([year] + [None] * len(annual_blocks) + [round(total, 1), "whole-farm total only"])
            else:
                row_kg = {bid: annual_kg.get((year, bid), 0.0) for bid in annual_blocks}
                total = sum(row_kg.values())
                as_.append([year] + [round(row_kg[bid], 1) if (year, bid) in annual_kg else None
                                      for bid in annual_blocks] + [round(total, 1), ""])
        as_.freeze_panes = "B2"
        as_.column_dimensions["A"].width = 8
        for col in range(2, len(header)):
            as_.column_dimensions[get_column_letter(col)].width = 11
        as_.column_dimensions[get_column_letter(len(header))].width = 20

    # --- Per-year sheets ---
    for year in years:
        year_days: dict = {}  # date -> {block_id: kg}
        year_blocks: set = set()
        for (y, block_id, d), kg in day_kg.items():
            if y != year:
                continue
            bucket = year_days.setdefault(d, {})
            bucket[block_id] = bucket.get(block_id, 0.0) + kg
            year_blocks.add(block_id)
        block_ids = sorted(year_blocks, key=_block_sort_key)

        ws = wb.create_sheet(str(year))
        header = ["Date", "Weekday"] + [block_label(bid) for bid in block_ids] + ["Total"]
        ws.append(header)
        for c in ws[1]:
            _style_header_cell(c)

        for d in sorted(year_days):
            row_kg = year_days[d]
            total = sum(row_kg.get(bid, 0.0) for bid in block_ids)
            ws.append([d, d.strftime("%A")] + [round(row_kg.get(bid, 0.0), 1) for bid in block_ids] +
                      [round(total, 1)])

        first_data_row = 2
        last_data_row = ws.max_row
        ws.append(["", "TOTAL"])
        footer_row = ws.max_row
        for i in range(len(block_ids) + 1):
            col_idx = 3 + i
            col_letter = get_column_letter(col_idx)
            cell = ws.cell(row=footer_row, column=col_idx,
                            value=f"=SUM({col_letter}{first_data_row}:{col_letter}{last_data_row})")
            cell.font = Font(bold=True)
        ws.cell(row=footer_row, column=2).font = Font(bold=True)

        for r in range(2, last_data_row + 1):
            ws.cell(row=r, column=1).number_format = "dd/mm/yyyy"

        ws.freeze_panes = "C2"
        for col in range(1, len(header) + 1):
            ws.column_dimensions[get_column_letter(col)].width = 11
        ws.column_dimensions["A"].width = 12
        ws.column_dimensions["B"].width = 11

    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    # Span the whole workbook, not just the daily sheets - it reaches back
    # to the earliest season-total year, well before the first daily one.
    filename = (f"Historical_Harvest_Data_{all_years[0]}_{all_years[-1]}.xlsx"
                if all_years else "Historical_Harvest_Data.xlsx")
    with open(os.path.join(REPORTS_DIR, filename), "wb") as f:
        f.write(data)
    return Response(content=data, media_type=XLSX_MEDIA,
                     headers={"Content-Disposition": f'attachment; filename="{filename}"'})
