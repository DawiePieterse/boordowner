"""Browser imports for pre-app harvest history.

Until now the only way to load these two tables was
scripts/import_historical_harvest.py and
scripts/import_historical_annual_yield.py, run from a shell on the server.
Both read one farm's own workbooks: a sheet per season, a column per block,
and a hardcoded set of sub-block ids to mark as estimated. That layout was
never a contract - it was the shape of the spreadsheet that farm happened
to keep - so it cannot be what a new customer is asked to produce.

These endpoints take a plain long-format table instead (one row per
measurement), parsed by the same excel_io.parse_uploaded_table() the blocks
and workers imports already use, so .csv and .xlsx both work and the column
names are documented in templates/README.md.

What deliberately did NOT move here is the hectare-ratio splitting those
scripts do. Deciding that a historical "block 8" column is really today's
8a and 8b in a 60/40 split is a judgement about one orchard's own past,
made once against that farm's records - exactly the class of thing that was
taken out of db.seed_defaults(). Whoever prepares the sheet makes that call
and flags the affected rows with `estimated`; the app does not guess.

Both imports REPLACE their whole table, matching the scripts. Neither table
is ever written by the app itself (see the docstrings on HistoricalHarvest
and HistoricalAnnualYield), so there is nothing of the farm's own to lose,
and it is what makes correcting a sheet and re-importing safe.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import func
from sqlmodel import Session, delete, select

from db import get_session
from excel_io import parse_uploaded_table
from models import HistoricalAnnualYield, HistoricalHarvest
from security import get_current_admin

router = APIRouter(prefix="/api", tags=["historical"])

# How many rows may be wrong before the import is refused outright rather
# than quietly loading the good ones. A couple of stray rows in a
# hand-kept spreadsheet is normal; a file where most rows fail is the wrong
# file, and importing its handful of parseable rows would replace a farm's
# whole history with fragments.
_MAX_REJECT_RATIO = 0.5


def earliest_history_season(session: Session) -> Optional[int]:
    """The oldest season this farm has loaded, across both history tables,
    or None if it has loaded neither.

    Lives here because these two tables are this module's, and it is read
    from two places that have nothing else to do with each other: the setup
    wizard, to suggest how far back to fetch weather, and the weather
    backfill, to say afterwards whether it went back far enough. Both are
    answering the same question - how much of this farm's own history the
    Risk indicator can actually be scored against.

    Aggregated in SQL: HistoricalHarvest is one row per block per day and
    reaches back years.
    """
    return min(
        (y for y in (session.exec(select(func.min(HistoricalHarvest.season_year))).one(),
                     session.exec(select(func.min(HistoricalAnnualYield.season_year))).one())
         if y is not None),
        default=None,
    )


def _first(row: dict, *names):
    """First non-empty value among `names`, tolerating the header spellings
    people actually produce (blank-padded, capitalised, spaced)."""
    lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    for name in names:
        value = lowered.get(name.strip().lower())
        if value is not None and str(value).strip() != "":
            return value
    return None


def _parse_date(value) -> date:
    """A calendar date from either an .xlsx cell (already a datetime) or a
    csv string. ISO first, then the two orderings a South African
    spreadsheet realistically holds - and never a bare guess between them:
    2026-03-04 is unambiguous, 03/04/2026 is not, so day-first wins there
    because that is what the locale writes."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {text!r}")


def _parse_bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _check_usable(rows: list, kept: int, rejected: list) -> None:
    """Refuse before the delete when there is nothing worth keeping.

    Both imports REPLACE their whole table, so "nothing to import" can never
    be allowed to mean "replace everything with nothing". The empty-file case
    is not hypothetical: the blank template the setup wizard hands out is
    itself a headings-only file, so downloading it, not filling it in, and
    uploading it by mistake would wipe every season the farm had - and report
    `imported: 0, rejected: 0`, which reads as success.
    """
    if not rows:
        raise HTTPException(400, "That file has no data rows - only column headings. "
                                  "Fill it in first. Nothing was changed.")
    if kept == 0:
        raise HTTPException(400, "No usable rows in that file - check the column headings "
                                  "against templates/README.md. Nothing was changed.")
    if rejected and len(rejected) > len(rows) * _MAX_REJECT_RATIO:
        raise HTTPException(400, f"{len(rejected)} of {len(rows)} rows could not be read "
                                  f"(first problem: {rejected[0]}). That usually means the wrong "
                                  f"file or the wrong columns. Nothing was changed.")


@router.post("/historical-harvest/import")
async def import_historical_harvest(file: UploadFile, session: Session = Depends(get_session),
                                     admin=Depends(get_current_admin)):
    """Daily per-block kg from seasons before the app existed.

    Columns: block_id, date, kg, and optionally season_year and estimated.
    season_year is derived from the date when absent - these are calendar
    dates within one picking season, and the daily table has always stored
    the two together (see models.HistoricalHarvest).
    """
    rows = await parse_uploaded_table(file)
    records, rejected = [], []
    for i, r in enumerate(rows, start=2):  # +2: header row, and 1-based
        try:
            block_id = _first(r, "block_id", "block", "id")
            kg = _first(r, "kg", "weight_kg")
            harvest_date = _parse_date(_first(r, "date", "harvest_date"))
            if block_id is None or kg is None:
                raise ValueError("block_id and kg are both required")
            season = _first(r, "season_year", "season", "year")
            records.append(HistoricalHarvest(
                block_id=str(block_id).strip(),
                harvest_date=harvest_date,
                season_year=int(season) if season is not None else harvest_date.year,
                kg=float(kg),
                estimated=_parse_bool(_first(r, "estimated") or False),
            ))
        except (ValueError, TypeError, AttributeError) as e:
            rejected.append(f"row {i}: {e}")

    _check_usable(rows, len(records), rejected)
    session.exec(delete(HistoricalHarvest))
    session.add_all(records)
    session.commit()
    seasons = sorted({r.season_year for r in records})
    return {"imported": len(records), "rejected": len(rejected), "rejected_detail": rejected[:5],
            "seasons": seasons}


@router.post("/historical-annual-yield/import")
async def import_historical_annual_yield(file: UploadFile, session: Session = Depends(get_session),
                                          admin=Depends(get_current_admin)):
    """Season totals from further back than daily records reach.

    Columns: season_year, kg, and optionally block_id and estimated. A blank
    block_id means a whole-farm total for that season with no block
    breakdown, which is the point of this table - older bookkeeping often
    predates the block register entirely, and inventing a mapping to today's
    blocks would be worse than keeping only the total.
    """
    rows = await parse_uploaded_table(file)
    records, rejected = [], []
    for i, r in enumerate(rows, start=2):
        try:
            season = _first(r, "season_year", "season", "year")
            kg = _first(r, "kg", "total_kg")
            if season is None or kg is None:
                raise ValueError("season_year and kg are both required")
            block_id = _first(r, "block_id", "block")
            records.append(HistoricalAnnualYield(
                block_id=str(block_id).strip() if block_id is not None else None,
                season_year=int(season),
                kg=float(kg),
                estimated=_parse_bool(_first(r, "estimated") or False),
            ))
        except (ValueError, TypeError) as e:
            rejected.append(f"row {i}: {e}")

    _check_usable(rows, len(records), rejected)
    session.exec(delete(HistoricalAnnualYield))
    session.add_all(records)
    session.commit()
    seasons = sorted({r.season_year for r in records})
    return {"imported": len(records), "rejected": len(rejected), "rejected_detail": rejected[:5],
            "seasons": seasons}

