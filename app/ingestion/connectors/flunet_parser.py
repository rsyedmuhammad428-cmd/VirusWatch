"""Pure parser/normaliser for the WHO FluNet CSV (view VIW_FNT).

Column facts: every column read here exists in WHO's FluNet data dictionary (view VIW_FNT, 53
fields; WHO's VIW_FLU_METADATA export, retrieved 2026-10-30), and
tests/ingestion/test_flunet_dictionary.py checks that against
tests/fixtures/flunet/viw_fnt_fields.csv. What has NOT been verified is a real
data download: header order, how `$filter` behaves for CSV output, and whether output is paginated.
The parser therefore validates the header strictly and refuses to guess: a missing required column
raises SchemaMismatchError and nothing is persisted.

Why the denominator is INF_ALL + INF_NEGATIVE and not SPEC_PROCESSED_NB: per the dictionary, INF_*
counts are "reported for year and week when the sample was taken", whereas SPEC_PROCESSED_NB
counts specimens "processed at the laboratory in the reporting week (can be different from the
week in which the sample has been taken)". Only the former pair share a sampling-week basis.

Normalisation rules (also documented in the README section "WHO FluNet ingestion"):

* One FluNet row = one (country, ISO week, ORIGIN_SOURCE stream). Streams stay separate series.
* The reporting period is the ISO week, Monday..Sunday, derived from ISO_YEAR + ISO_WEEK.
  If ISO_WEEKSTARTDATE is present it must agree, otherwise the row is rejected.
* A blank cell means "not reported" and is never converted to 0. For the core indicators this
  yields an explicit `missing` observation; for subtype/lineage counts the row is simply omitted.
* influenza_positivity = INF_ALL / (INF_ALL + INF_NEGATIVE) * 100, i.e. positives among specimens
  with a definite result. It is derived by VirusWatch (flagged), stored with numerator and
  denominator, and rounded to 4 decimals. SPEC_PROCESSED_NB is kept separately; when it differs
  from the denominator the observation is flagged rather than silently "fixed".
* Counts must be non-negative integers; anything else rejects that row.
* Data for other countries / earlier years than requested is skipped, not validated.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from app.ingestion.countries import SUPPORTED_COUNTRIES
from app.ingestion.errors import IngestionError, SchemaMismatchError
from app.ingestion.models import (
    IndicatorType,
    ObservationRecord,
    ParseResult,
    RowRejection,
    SurveillanceStream,
)

PARSER_VERSION = "flunet-1"

REQUIRED_COLUMNS = (
    "COUNTRY_CODE",
    "ISO_YEAR",
    "ISO_WEEK",
    "ORIGIN_SOURCE",
    "INF_ALL",
    "INF_NEGATIVE",
)
WEEK_START_COLUMN = "ISO_WEEKSTARTDATE"  # optional cross-check only
SPECIMENS_COLUMN = "SPEC_PROCESSED_NB"  # optional

STREAMS: dict[str, SurveillanceStream] = {
    "SENTINEL": "sentinel",
    "NONSENTINEL": "nonsentinel",
    "NOTDEFINED": "undefined",
}

# (source column, canonical pathogen). INF_ALL is the all-influenza count (pathogen None).
PATHOGEN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("INF_A", "influenza_a"),
    ("INF_B", "influenza_b"),
    ("AH1N12009", "influenza_a_h1n1pdm09"),
    ("AH3", "influenza_a_h3n2"),
    ("BYAM", "influenza_b_yamagata"),
)
# B/Victoria is reported as four deletion-category columns; their sum is only meaningful when
# all four are present (a blank must not be treated as 0).
VICTORIA_COLUMNS = ("BVIC_2DEL", "BVIC_3DEL", "BVIC_NODEL", "BVIC_DELUNK")

SMALL_DENOMINATOR = 10  # a caution threshold for unstable percentages, not a clinical standard

FLAG_DERIVED = "derived_by_viruswatch"
FLAG_NO_TESTS = "no_tests_reported"
FLAG_INPUT_MISSING = "input_missing"
FLAG_DENOMINATOR_DIFFERS = "denominator_differs_from_specimens_processed"
FLAG_SMALL_DENOMINATOR = "small_denominator"
FLAG_COMPONENT_MISMATCH = "component_sum_mismatch"

_PCT = Decimal("0.0001")


class _RowError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _clip(value: str, n: int = 40) -> str:
    return value if len(value) <= n else value[: n - 1] + "…"


def _parse_count(raw: str | None, column: str) -> Decimal | None:
    """Blank/absent -> None (not reported). Otherwise a non-negative whole number."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw == "":
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise _RowError("invalid_count", f"{column}={_clip(raw)!r} is not a number") from None
    if not value.is_finite() or value < 0 or value != value.to_integral_value():
        raise _RowError("invalid_count", f"{column}={_clip(raw)!r} is not a non-negative integer")
    return value.to_integral_value()


@dataclass
class _Seen:
    row_numbers: list[int]
    fingerprint: tuple[str, ...]
    observations: list[ObservationRecord]
    conflicted: bool = False


@dataclass
class _Context:
    columns: tuple[str, ...]
    source_url: str
    countries: frozenset[str]
    since_year: int | None
    result: ParseResult = field(default_factory=ParseResult)
    seen: dict[tuple[str, int, int, str], _Seen] = field(default_factory=dict)
    week_start_unparseable_warned: bool = False

    @property
    def count_columns_in_use(self) -> tuple[str, ...]:
        wanted = (
            "INF_ALL",
            "INF_NEGATIVE",
            SPECIMENS_COLUMN,
            *(column for column, _ in PATHOGEN_COLUMNS),
            *VICTORIA_COLUMNS,
        )
        return tuple(c for c in wanted if c in self.columns)


def parse_flunet_csv(
    content: bytes,
    *,
    countries: frozenset[str],
    since_year: int | None,
    source_url: str,
) -> ParseResult:
    unsupported = countries - SUPPORTED_COUNTRIES.keys()
    if unsupported:
        raise IngestionError(f"Unsupported countries passed to parser: {sorted(unsupported)}")

    try:
        text = io.TextIOWrapper(
            io.BytesIO(content), encoding="utf-8-sig", newline="", errors="strict"
        )
        reader = csv.reader(text)
        header_row = next(reader, None)
        if header_row is None:
            raise SchemaMismatchError("The file is empty.")
        columns = tuple(h.strip().upper() for h in header_row)
        _validate_header(columns)

        ctx = _Context(
            columns=columns, source_url=source_url, countries=countries, since_year=since_year
        )
        ctx.result.columns = columns
        _add_column_warnings(ctx)
        index = {name: i for i, name in enumerate(columns)}

        for row in reader:
            if not row:  # blank line
                continue
            row_number = reader.line_num
            ctx.result.rows_read += 1
            _handle_row(ctx, index, row, row_number)
    except UnicodeDecodeError as exc:
        raise SchemaMismatchError("The file is not valid UTF-8 text.") from exc
    except csv.Error as exc:
        raise SchemaMismatchError(f"The file is not parseable CSV ({exc}).") from exc

    _resolve_duplicates(ctx)
    return ctx.result


def _validate_header(columns: tuple[str, ...]) -> None:
    if len(set(columns)) != len(columns):
        raise SchemaMismatchError("The header contains duplicate column names.")
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        preview = ", ".join(columns[:12]) + (", …" if len(columns) > 12 else "")
        raise SchemaMismatchError(
            f"Required column(s) missing: {', '.join(missing)}. "
            f"File has {len(columns)} columns: {preview}. "
            "Compare against WHO's VIW_FLU_METADATA data dictionary."
        )


def _add_column_warnings(ctx: _Context) -> None:
    absent = [
        c for c in (SPECIMENS_COLUMN, *(c for c, _ in PATHOGEN_COLUMNS)) if c not in ctx.columns
    ]
    for column in absent:
        ctx.result.warnings.append(f"optional column absent, indicator not produced: {column}")
    if any(c not in ctx.columns for c in VICTORIA_COLUMNS):
        ctx.result.warnings.append("optional BVIC_* columns incomplete: B/Victoria not produced")
    if WEEK_START_COLUMN not in ctx.columns:
        ctx.result.warnings.append(f"{WEEK_START_COLUMN} absent: week-start cross-check skipped")


def _handle_row(ctx: _Context, index: dict[str, int], row: list[str], row_number: int) -> None:
    result = ctx.result
    if len(row) != len(ctx.columns):
        result.rejections.append(
            RowRejection(
                row_number, "malformed_row", f"{len(row)} fields, expected {len(ctx.columns)}"
            )
        )
        return

    def cell(name: str) -> str | None:
        i = index.get(name)
        return row[i] if i is not None else None

    iso3 = (cell("COUNTRY_CODE") or "").strip().upper()
    if iso3 not in ctx.countries:
        result.rows_out_of_scope += 1
        return

    try:
        year, week = _parse_year_week(cell("ISO_YEAR"), cell("ISO_WEEK"))
        if ctx.since_year is not None and year < ctx.since_year:
            result.rows_out_of_scope += 1
            return
        start = _week_start(year, week)
        _check_week_start(ctx, cell(WEEK_START_COLUMN), start)
        origin_raw = (cell("ORIGIN_SOURCE") or "").strip().upper()
        stream = STREAMS.get(origin_raw)
        if stream is None:
            raise _RowError("unknown_stream", f"ORIGIN_SOURCE={_clip(origin_raw)!r}")
        counts = {name: _parse_count(cell(name), name) for name in ctx.count_columns_in_use}
    except _RowError as err:
        result.rejections.append(RowRejection(row_number, err.reason, err.detail))
        return

    fingerprint = tuple((cell(n) or "").strip() for n in ctx.count_columns_in_use)
    key = (iso3, year, week, stream)
    if existing := ctx.seen.get(key):
        existing.row_numbers.append(row_number)
        if existing.fingerprint == fingerprint:
            result.rows_duplicate_identical += 1
        else:
            existing.conflicted = True
        return

    observations = _observations_for_row(
        ctx,
        iso3=iso3,
        year=year,
        week=week,
        start=start,
        stream=stream,
        origin_raw=origin_raw,
        counts=counts,
    )
    ctx.seen[key] = _Seen([row_number], fingerprint, observations)


def _parse_year_week(year_raw: str | None, week_raw: str | None) -> tuple[int, int]:
    try:
        return int((year_raw or "").strip()), int((week_raw or "").strip())
    except ValueError:
        raise _RowError(
            "invalid_iso_week",
            f"ISO_YEAR={_clip(year_raw or '')!r} ISO_WEEK={_clip(week_raw or '')!r}",
        ) from None


def _week_start(year: int, week: int) -> date:
    try:
        return date.fromisocalendar(year, week, 1)
    except ValueError:
        raise _RowError("invalid_iso_week", f"{year}-W{week}") from None


def _check_week_start(ctx: _Context, raw: str | None, expected: date) -> None:
    if raw is None or not raw.strip():
        return
    try:
        given = date.fromisoformat(raw.strip()[:10])
    except ValueError:
        if not ctx.week_start_unparseable_warned:
            ctx.week_start_unparseable_warned = True
            ctx.result.warnings.append(
                f"{WEEK_START_COLUMN} has an unrecognised date format; cross-check skipped"
            )
        return
    if given != expected:
        raise _RowError(
            "week_start_mismatch", f"{WEEK_START_COLUMN}={given} but ISO week starts {expected}"
        )


@dataclass(frozen=True)
class _RowFacts:
    """Fields shared by every observation produced from one FluNet row."""

    country_code: str
    stream: SurveillanceStream
    period_start: date
    source_record_id: str
    source_url: str
    origin_raw: str

    def observation(
        self,
        indicator: str,
        indicator_type: IndicatorType,
        value: Decimal | None,
        unit: str,
        *,
        columns: tuple[str, ...],
        pathogen: str | None = None,
        numerator: Decimal | None = None,
        denominator: Decimal | None = None,
        flags: tuple[str, ...] = (),
    ) -> ObservationRecord:
        return ObservationRecord(
            country_code=self.country_code,
            disease="influenza",
            pathogen=pathogen,
            indicator=indicator,
            indicator_type=indicator_type,
            surveillance_stream=self.stream,
            reporting_period_start=self.period_start,
            reporting_period_end=self.period_start + timedelta(days=6),
            value_numeric=value,
            numerator=numerator,
            denominator=denominator,
            unit=unit,
            data_status="missing" if value is None else "reported",
            quality_flags=flags,
            source_record_id=self.source_record_id,
            source_url=self.source_url,
            metadata={"source_columns": list(columns), "origin_source": self.origin_raw},
        )


def _observations_for_row(
    ctx: _Context,
    *,
    iso3: str,
    year: int,
    week: int,
    start: date,
    stream: SurveillanceStream,
    origin_raw: str,
    counts: dict[str, Decimal | None],
) -> list[ObservationRecord]:
    facts = _RowFacts(
        country_code=SUPPORTED_COUNTRIES[iso3].iso2,
        stream=stream,
        period_start=start,
        source_record_id=f"{iso3}|{year}-W{week:02d}|{origin_raw}",
        source_url=ctx.source_url,
        origin_raw=origin_raw,
    )
    out: list[ObservationRecord] = []
    inf_all, inf_neg = counts["INF_ALL"], counts["INF_NEGATIVE"]
    processed = counts.get(SPECIMENS_COLUMN)

    if SPECIMENS_COLUMN in counts:
        out.append(
            facts.observation(
                "specimens_processed",
                "laboratory_count",
                processed,
                "specimens",
                columns=(SPECIMENS_COLUMN,),
            )
        )

    inf_a, inf_b = counts.get("INF_A"), counts.get("INF_B")
    components_disagree = (
        inf_all is not None and inf_a is not None and inf_b is not None and inf_a + inf_b != inf_all
    )
    out.append(
        facts.observation(
            "influenza_positive_count",
            "laboratory_count",
            inf_all,
            "specimens",
            columns=("INF_ALL",),
            flags=(FLAG_COMPONENT_MISMATCH,) if components_disagree else (),
        )
    )
    out.append(_positivity(facts, inf_all, inf_neg, processed))

    for column, pathogen in PATHOGEN_COLUMNS:
        value = counts.get(column)
        if value is not None:  # blank subtype counts are omitted, never zero-filled
            out.append(
                facts.observation(
                    "influenza_positive_count",
                    "laboratory_count",
                    value,
                    "specimens",
                    pathogen=pathogen,
                    columns=(column,),
                )
            )

    victoria = [counts.get(c) for c in VICTORIA_COLUMNS]
    if all(v is not None for v in victoria):
        total = sum((v for v in victoria if v is not None), Decimal(0))
        out.append(
            facts.observation(
                "influenza_positive_count",
                "laboratory_count",
                total,
                "specimens",
                pathogen="influenza_b_victoria",
                columns=VICTORIA_COLUMNS,
            )
        )
    return out


def _positivity(
    facts: _RowFacts,
    inf_all: Decimal | None,
    inf_neg: Decimal | None,
    processed: Decimal | None,
) -> ObservationRecord:
    columns = ("INF_ALL", "INF_NEGATIVE")

    def undefined(flag: str, denominator: Decimal | None = None) -> ObservationRecord:
        return facts.observation(
            "influenza_positivity",
            "laboratory_positivity",
            None,
            "percent",
            columns=columns,
            numerator=inf_all,
            denominator=denominator,
            flags=(FLAG_DERIVED, flag),
        )

    if inf_all is None or inf_neg is None:
        return undefined(FLAG_INPUT_MISSING)
    denominator = inf_all + inf_neg
    if denominator == 0:
        return undefined(FLAG_NO_TESTS, denominator)

    flags = [FLAG_DERIVED]
    if denominator < SMALL_DENOMINATOR:
        flags.append(FLAG_SMALL_DENOMINATOR)
    if processed is not None and processed != denominator:
        flags.append(FLAG_DENOMINATOR_DIFFERS)
    value = (inf_all * 100 / denominator).quantize(_PCT, rounding=ROUND_HALF_EVEN)
    return facts.observation(
        "influenza_positivity",
        "laboratory_positivity",
        value,
        "percent",
        columns=columns,
        numerator=inf_all,
        denominator=denominator,
        flags=tuple(flags),
    )


def _resolve_duplicates(ctx: _Context) -> None:
    """Identical repeats were already dropped; conflicting repeats are ambiguous, so reject all."""
    result = ctx.result
    for seen in ctx.seen.values():
        if seen.conflicted:
            for row_number in seen.row_numbers:
                result.rejections.append(
                    RowRejection(
                        row_number,
                        "conflicting_duplicate",
                        "same country/week/stream appears with different values",
                    )
                )
        else:
            result.observations.extend(seen.observations)
    result.rejections.sort(key=lambda r: r.row_number)
