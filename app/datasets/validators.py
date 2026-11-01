"""Pure validators for the supplied non-connector datasets.

Each validator takes already-parsed rows (lists of dicts from csv.DictReader) and returns a report.
Values that cannot be trusted become None plus an explicit flag; they are never replaced by 0 and
never silently corrected. Nothing here writes to a database.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# --------------------------------------------------------------------------------------------
# COVID-19 daily cases by province, Pakistan, 2020 (OpenData Pakistan CSV; no data dictionary)
# --------------------------------------------------------------------------------------------

COVID_PROVINCES = frozenset({"ICT", "Punjab", "Sindh", "KP", "Balochistan", "AJK", "GB"})
_COVID_DATE_FORMATS = ("%m/%d/%Y", "%d-%b-%Y")
_INT = re.compile(r"-?\d+")

# Only columns whose meaning can be defended are mapped. "Cumulative", "New (last 24 hrs)" and
# "Tests performed ..." are NOT mapped: in the supplied file they reproduce cumulative/daily *test*
# counts in some rows and case-like numbers in others, and the source gives no dictionary.
COVID_MAPPED = {
    "cumulative test positive": "cumulative_confirmed",
    "discharged": "discharged",
    "expired": "expired",
}


@dataclass(frozen=True)
class CovidDay:
    report_date: date
    region: str
    cumulative_confirmed: int | None
    discharged: int | None
    expired: int | None
    flags: tuple[str, ...] = ()


@dataclass
class CovidReport:
    rows_read: int = 0
    days: list[CovidDay] = field(default_factory=list)
    rejected: Counter[str] = field(default_factory=Counter)
    date_gaps: list[tuple[date, date]] = field(default_factory=list)
    flag_counts: Counter[str] = field(default_factory=Counter)


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def parse_covid_date(text: str) -> date | None:
    for fmt in _COVID_DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()  # noqa: DTZ007 - calendar date only
        except ValueError:
            continue
    return None


def _count(text: str | None, flags: list[str], column: str) -> int | None:
    value = (text or "").strip()
    if value == "":
        return None
    if not _INT.fullmatch(value):
        flags.append(f"non_numeric:{column}")
        return None
    return int(value)


def validate_covid_2020(rows: Iterable[Mapping[str, str]]) -> CovidReport:
    report = CovidReport()
    parsed: list[CovidDay] = []
    seen: set[tuple[date, str]] = set()
    for raw in rows:
        report.rows_read += 1
        row = {_norm_header(k): v for k, v in raw.items() if k is not None}
        day = parse_covid_date(row.get("date", ""))
        if day is None:
            report.rejected["invalid_date"] += 1
            continue
        region = (row.get("region") or "").strip()
        if not region:
            report.rejected["missing_region"] += 1
            continue
        if (day, region) in seen:
            report.rejected["duplicate_date_region"] += 1
            continue
        seen.add((day, region))
        flags: list[str] = []
        values = {
            target: _count(row.get(source), flags, target)
            for source, target in COVID_MAPPED.items()
        }
        if region not in COVID_PROVINCES:
            flags.append("non_province_region")
        for name, value in values.items():
            if value is not None and value < 0:
                flags.append(f"negative:{name}")
                values[name] = None
        parsed.append(CovidDay(day, region, flags=tuple(flags), **values))

    by_region: dict[str, list[CovidDay]] = defaultdict(list)
    for item in parsed:
        by_region[item.region].append(item)
    final: list[CovidDay] = []
    for items in by_region.values():
        items.sort(key=lambda d: d.report_date)
        previous: CovidDay | None = None
        for item in items:
            extra: list[str] = []
            current = item.cumulative_confirmed
            before = previous.cumulative_confirmed if previous else None
            if current is not None and before is not None:
                if current < before:
                    extra.append("cumulative_decreased")
                elif current == before and current > 0:
                    extra.append("unchanged_cumulative")
            final.append(
                CovidDay(
                    item.report_date,
                    item.region,
                    item.cumulative_confirmed,
                    item.discharged,
                    item.expired,
                    item.flags + tuple(extra),
                )
            )
            if current is not None:
                previous = item
    final.sort(key=lambda d: (d.report_date, d.region))
    report.days = final
    for item in final:
        report.flag_counts.update(item.flags)
    dates = sorted({d.report_date for d in final})
    for a, b in zip(dates, dates[1:], strict=False):
        if b - a > timedelta(days=1):
            report.date_gaps.append((a, b))
    return report


# --------------------------------------------------------------------------------------------
# WHO Global Health Observatory export (health context, never surveillance)
# --------------------------------------------------------------------------------------------

GHO_KEY_COLUMNS = (
    "GHO (CODE)",
    "COUNTRY (CODE)",
    "STARTYEAR",
    "ENDYEAR",
    "DIMENSION (TYPE)",
    "DIMENSION (CODE)",
)


@dataclass
class GhoReport:
    rows_read: int = 0
    blank_numeric: int = 0
    identical_duplicate_groups: int = 0
    ambiguous_key_groups: int = 0
    unique_keys: int = 0
    ambiguous_codes: Counter[str] = field(default_factory=Counter)


def parse_gho_numeric(row: Mapping[str, str]) -> float | None:
    """Use the 'Numeric' column. The display 'Value' column has space thousands separators and
    embedded confidence intervals ("17 722", "128.8 [121.2-136.7]") and must not be parsed."""
    text = (row.get("Numeric") or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def validate_gho(rows: Iterable[Mapping[str, str]]) -> GhoReport:
    report = GhoReport()
    groups: dict[tuple[str, ...], list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        report.rows_read += 1
        if parse_gho_numeric(row) is None:
            report.blank_numeric += 1
        groups[tuple(row.get(c, "") for c in GHO_KEY_COLUMNS)].append(row)
    for key, members in groups.items():
        if len(members) == 1:
            report.unique_keys += 1
        elif len({tuple(sorted(m.items())) for m in members}) == 1:
            report.identical_duplicate_groups += 1
        else:
            # Same visible key, different values: the export dropped a disaggregating dimension
            # (fuel type, age band, ...). Such rows cannot be attributed and must not be loaded.
            report.ambiguous_key_groups += 1
            report.ambiguous_codes[key[0]] += 1
    return report


def gho_unique_rows(
    rows: Iterable[Mapping[str, str]], allow_codes: frozenset[str]
) -> list[Mapping[str, str]]:
    """Rows of allow-listed indicators whose key is unique and whose numeric value is present."""
    rows = list(rows)
    counts = Counter(tuple(r.get(c, "") for c in GHO_KEY_COLUMNS) for r in rows)
    return [
        r
        for r in rows
        if r.get("GHO (CODE)") in allow_codes
        and counts[tuple(r.get(c, "") for c in GHO_KEY_COLUMNS)] == 1
        and parse_gho_numeric(r) is not None
    ]


# --------------------------------------------------------------------------------------------
# Health-facility points (reference data only)
# --------------------------------------------------------------------------------------------

PAKISTAN_BBOX = (60.0, 23.0, 78.0, 38.0)  # lon_min, lat_min, lon_max, lat_max (generous)


@dataclass
class FacilityReport:
    rows_read: int = 0
    missing_coordinates: int = 0
    outside_pakistan: int = 0
    missing_name: int = 0
    duplicate_ids: int = 0
    earliest_edit: str | None = None
    latest_edit: str | None = None


def validate_facilities(rows: Iterable[Mapping[str, str]]) -> FacilityReport:
    report = FacilityReport()
    seen: set[str] = set()
    stamps: list[str] = []
    lon0, lat0, lon1, lat1 = PAKISTAN_BBOX
    for row in rows:
        report.rows_read += 1
        x, y = (row.get("X") or "").strip(), (row.get("Y") or "").strip()
        if not x or not y:
            report.missing_coordinates += 1
        else:
            try:
                if not (lon0 <= float(x) <= lon1 and lat0 <= float(y) <= lat1):
                    report.outside_pakistan += 1
            except ValueError:
                report.missing_coordinates += 1
        if not (row.get("name") or "").strip():
            report.missing_name += 1
        osm_id = (row.get("osm_id") or "").strip()
        if osm_id in seen:
            report.duplicate_ids += 1
        seen.add(osm_id)
        if row.get("changeset_timestamp"):
            stamps.append(row["changeset_timestamp"])
    if stamps:
        report.earliest_edit, report.latest_edit = min(stamps), max(stamps)
    return report
