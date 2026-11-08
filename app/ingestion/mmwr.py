"""MMWR epidemiological week arithmetic (CDC's week numbering, not ISO 8601).

CDC's MMWR calendar week runs Sunday through Saturday, unlike WHO's ISO week (Monday through
Sunday) used by app.ingestion.connectors.flunet_parser. Reusing `date.fromisocalendar` for CDC
data would silently shift every date by up to a few days and must never happen.

MMWR week 1 of a year is defined as the week (Sunday-Saturday) that contains January 4th of that
year -- equivalently, the first week with at least four of its days in the new year. This matches
CDC's own definition (https://ndc.services.cdc.gov/wp-content/uploads/MMWR_Week_overview.pdf) and
is the algorithm the actively-maintained `MMWRweek` R package and CDC's own EPI_WEEK stored
procedures implement.
"""

from __future__ import annotations

from datetime import date, timedelta


class InvalidMMWRWeek(ValueError):
    pass


def mmwr_week_start(year: int, week: int) -> date:
    """The Sunday that begins MMWR week `week` of MMWR year `year`."""
    if not 1900 <= year <= 2100:
        raise InvalidMMWRWeek(f"year {year} out of range")
    if not 1 <= week <= 53:
        raise InvalidMMWRWeek(f"week {week} out of range (1-53)")
    jan4 = date(year, 1, 4)
    # Python's weekday(): Monday=0 .. Sunday=6. Days since the most recent Sunday:
    days_since_sunday = (jan4.weekday() + 1) % 7
    week1_start = jan4 - timedelta(days=days_since_sunday)
    start = week1_start + timedelta(weeks=week - 1)
    # Week 53 only exists in some years; reject it when it would spill into the following
    # year's MMWR week 1 instead of silently returning a date outside this MMWR year.
    if week == 53:
        next_week1 = mmwr_week_start(year + 1, 1)
        if start >= next_week1:
            raise InvalidMMWRWeek(f"{year} has no MMWR week 53")
    return start


def mmwr_week_end(year: int, week: int) -> date:
    return mmwr_week_start(year, week) + timedelta(days=6)
