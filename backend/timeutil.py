"""Timezone helpers.

Copied verbatim from Boord (../Boord/backend/timeutil.py). Every timestamp in
Boord's data is recorded in UTC and stored naive (SQLite drops the tzinfo),
but the people using the app think entirely in farm time - SAST on the
server's own clock. These helpers are the single place that bridges the two,
so day filters cover the day the farm actually worked and exported reports
read in local time rather than UTC.
"""
from datetime import date, datetime, time, timezone
from typing import Optional


def as_utc(dt: datetime) -> datetime:
    """Attach UTC to a timestamp that came out of the database naive."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """A stored timestamp expressed in the server's local timezone."""
    return as_utc(dt).astimezone() if dt is not None else None


def local_str(dt: Optional[datetime], fallback: str = "") -> str:
    """Local 'YYYY-MM-DD HH:MM' for reports and exports."""
    local = to_local(dt)
    return local.strftime("%Y-%m-%d %H:%M") if local else fallback


def day_bounds(start_day: date, end_day: Optional[date] = None) -> tuple:
    """The naive-UTC range matching a span of local calendar days.

    Combining a date with midnight directly would cut the day on UTC
    boundaries - 02:00 to 02:00 local in SAST - so anything picked before
    sunrise landed in the previous day's totals.
    """
    end_day = end_day if end_day is not None else start_day
    start = datetime.combine(start_day, time.min).astimezone()
    end = datetime.combine(end_day, time.max).astimezone()
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))


# --------------------------------------------------------------------------- #
# Seasons
# --------------------------------------------------------------------------- #
# A season is a recurring anchor - a month and a day - not a calendar year,
# because a litchi season crosses the new year. Boord stores that anchor on
# SystemSetting (season_start_month / season_start_day, added in v3.0) and
# labels each season by the year it starts in; current_harvest_year is that
# derived label.
#
# These two are the Python twins of Boord's Boord.seasonYearFor and its
# Season-preset arithmetic in ../Boord/frontend/shared/api.js. Keep them
# identical: if the two apps disagree about which season a date falls in,
# the Owner app's season-vs-history comparisons are quietly wrong rather
# than visibly broken.
def season_year_for(d: date, month: int, day: int) -> int:
    """Which season a date belongs to, labelled by the year that season began."""
    return d.year if d >= date(d.year, month, day) else d.year - 1


def season_day(d: date, season_year: int, month: int, day: int) -> int:
    """Days since the season's anchor, 1-indexed (1 = the anchor date itself).

    Charts anchor here rather than to 1 January so the axis spends its width
    on the months that actually have picking in them.
    """
    return (d - date(season_year, month, day)).days + 1
