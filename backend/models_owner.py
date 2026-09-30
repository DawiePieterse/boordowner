"""The Boord Owner app's OWN database schema - the tables this process
writes. Separate from Boord's schema (models_boord.py), which this app only
ever reads.

WeatherHistory / HistoricalHarvest / HistoricalAnnualYield are copied
verbatim from Boord at the commit that removed them (git 2226750), so the
import scripts and the recovered selftests keep working unchanged.

There is no user table: the app has no sign-in. Reaching it at all is the
whole of its access control, and that is the tailnet's job - see the
Access section of README.md.
"""
from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class HistoricalHarvest(SQLModel, table=True):
    """Daily per-block kg from seasons before this app existed (2020-2025),
    imported once from the farm's own record spreadsheet - see
    scripts/import_historical_harvest.py for provenance, and that script's
    source workbook's own Notes sheet for the block-split-by-hectare-ratio
    and column-typo caveats behind a few of these rows. Never written to by
    the app itself; re-running the import script replaces the table wholesale."""
    id: Optional[int] = Field(default=None, primary_key=True)
    # No foreign_key= (unlike Boord's original): block lives in Boord's
    # database, not this one, so the reference is unenforceable here and only
    # confuses create_all. block_id is still Boord's block label, e.g. "8a".
    block_id: Optional[str] = None
    harvest_date: date
    season_year: int
    kg: float
    estimated: bool = False  # true where a combined historical block column was split by hectare ratio


class HistoricalAnnualYield(SQLModel, table=True):
    """ANNUAL kg totals for seasons even further back (1987-2019) than
    HistoricalHarvest's daily records - the farm's older bookkeeping only
    tracked totals per season, not per day, that far back. See
    scripts/import_historical_annual_yield.py for provenance. Two different
    grains, both from the same source workbook: 2012-2019 is PER-BLOCK
    (block_id set, same block-split-by-hectare-ratio caveat as
    HistoricalHarvest); 1987-2009 is a single WHOLE-FARM row per year
    (block_id NULL) - those years' own block numbering predates today's
    block register with no reliable mapping, so only the farm-wide total
    is kept. Reference-only: with no daily breakdown to align by
    season-day, and no weather data before 2020 to drive it, this doesn't
    feed the Analysis tab or Risk indicator - it only appears as an extra
    sheet on the Historical Harvest Data export. Never written to by the
    app itself; re-running the import script replaces the table wholesale."""
    id: Optional[int] = Field(default=None, primary_key=True)
    block_id: Optional[str] = None  # NULL = whole-farm total, no block breakdown (see note on HistoricalHarvest.block_id)
    season_year: int
    kg: float
    estimated: bool = False  # true where a combined historical block column was split by hectare ratio


class WeatherHistory(SQLModel, table=True):
    """Hourly weather for the farm's location, back to 1987, pulled from
    two different Open-Meteo APIs depending on era - see backend/weather.py's
    fetch_historical_hourly() (2020 onward) / fetch_archive_hourly()
    (1987-2019 - soil_temp_6cm_c and uv_index are always NULL that far
    back, that API never carries them) and shared parse_hourly_rows().
    Filled by scripts/import_historical_weather.py (2020 onward) and
    scripts/import_historical_weather_archive.py (1987-2019) - each only
    replaces its own date range, so they compose safely in either order -
    and kept current day-to-day by weather.sync_recent_weather()
    (append-only, run as a side effect of loading the weather or risk
    figures). Only 2020-2025 actually drives anything (the Risk indicator
    and Harvest Forecast are fixed to that reference range - see
    routers/risk.py); 1987-2019 is reference-only, for the weather chart."""
    id: Optional[int] = Field(default=None, primary_key=True)
    # Unlike every other datetime column in this app (naive UTC - see
    # timeutil.py), this is naive LOCAL farm time: Open-Meteo was queried
    # with timezone=auto, so its "time" strings are already in the farm's
    # own timezone. Never pass this through timeutil.to_local() - use
    # timestamp.date() directly, or it'll be shifted a second time.
    timestamp: datetime = Field(index=True, unique=True)
    temp_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    dew_point_c: Optional[float] = None
    precipitation_mm: Optional[float] = None
    weather_code: Optional[int] = None
    condition: str = ""
    wind_speed_kmh: Optional[float] = None
    soil_temp_6cm_c: Optional[float] = None
    uv_index: Optional[float] = None
    sunshine_duration_s: Optional[float] = None
    # The coordinates this hour was fetched FOR, not a measurement.
    # weather.different_location() is the one definition of "not this farm's",
    # and the backfill deletes what it matches. NULL means provenance unknown
    # (rows predating this column) and counts as a different location.
    lat: Optional[float] = None
    lon: Optional[float] = None


class YieldEstimate(SQLModel, table=True):
    """One version of the owner's pre-season crop estimate for a season:
    a kg-per-tree figure per block, judged in the orchard and set against
    that block's own history (see routers/estimate.py). A season usually
    has several - the January estimate, a revision after fruit set, one
    mid-picking - and all of them are kept, so the owner can see how the
    call moved and how the first one compared with what was picked.

    Unlike every other owner table this one IS written by the app: it is
    the owner's own judgement, entered on the Estimate tab. There is still
    no sign-in - the tailnet is the access control (README.md, Access)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    season_year: int = Field(index=True)  # labelled like Boord's seasons: the year it starts in
    name: str = ""
    notes: str = ""
    created_at: datetime   # naive UTC, like every datetime here but WeatherHistory's
    updated_at: datetime
    # The Risk tab's weather-driven Harvest Forecast as it stood when this
    # version was saved (current season only). Kept because it cannot be
    # rebuilt later: the live forecast and today's station reading are never
    # stored, and next season the kg line refits with this season in it.
    # Sent by the client from the figures it was showing - the save does not
    # re-run the model. All NULL = saved without one (or before these columns
    # existed; _ensure_owner_columns adds them as NULL to older rows).
    forecast_favorable_kg: Optional[float] = None
    forecast_expected_kg: Optional[float] = None
    forecast_unfavorable_kg: Optional[float] = None
    forecast_built_at: Optional[datetime] = None   # naive UTC, when the server built that forecast
    forecast_live: Optional[bool] = None           # False = the live weather forecast was unavailable
    forecast_settled: Optional[int] = None         # weather factors with nothing left to assume


class YieldEstimateBlock(SQLModel, table=True):
    """One block's line in a YieldEstimate. The tree count is copied from
    Boord's block register when the line is made and kept here, so a later
    change to the register (trees removed, a block split) doesn't silently
    rewrite what an old estimate said. Estimated kg is trees x kg_per_tree,
    worked out on read rather than stored."""
    id: Optional[int] = Field(default=None, primary_key=True)
    estimate_id: int = Field(index=True)  # YieldEstimate.id
    block_id: str   # Boord's block label, e.g. "8a" (see HistoricalHarvest.block_id)
    trees: int = 0
    kg_per_tree: Optional[float] = None   # NULL = not estimated yet
    note: str = ""


class YieldEstimatePack(SQLModel, table=True):
    """One line of a YieldEstimate version's pack-out mix: what share of the
    net picked kg the owner expects to go to a channel and pack type, and
    how many kg one carton of it takes (give-away included). The owner's
    judgement, stored with the version like YieldEstimateBlock's tree counts,
    so editing next season's mix never rewrites what an old version said.
    Estimation only: cartons are worked out on read, and nothing here is a
    packing record."""
    id: Optional[int] = Field(default=None, primary_key=True)
    estimate_id: int = Field(index=True)   # YieldEstimate.id
    position: int = 0                      # display order
    channel: str                           # free text, e.g. "Export sea"
    pack_type: str = ""                    # e.g. "4.5 kg"
    kg_per_carton: Optional[float] = None  # NULL = not cartoned (juice, rejects)
    share_pct: float = 0.0                 # % of the estimate's net picked kg
    note: str = ""


class ForecastSnapshot(SQLModel, table=True):
    """One row per day: the Expected kg the Harvest Forecast gave that day.
    Written by risk.record_forecast_snapshot() (a background job and any
    forecast build), read back for the Expected card's last-7-days trend.
    Kept because a past forecast cannot be rebuilt later - the live weather
    forecast it used is never stored. A day is overwritten by the day's
    later builds, so the row holds the day's latest figure. Days the live
    forecast was unavailable are skipped, not stored as a lesser number."""
    id: Optional[int] = Field(default=None, primary_key=True)
    snapshot_date: date = Field(index=True, unique=True)   # the farm's local date
    season_year: int
    expected_kg: float
    built_at: datetime   # naive UTC
