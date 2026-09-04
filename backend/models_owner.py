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
