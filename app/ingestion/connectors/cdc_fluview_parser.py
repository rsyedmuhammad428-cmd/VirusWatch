"""Pure parsers for CDC FluView's three weekly CSV releases (ILINet + WHO/NREVSS clinical and
public-health laboratory reports).

Findings used here were recorded in docs/source_notes_cdc_fluview.md after inspecting the real
files the project owner supplied (not a live download -- no connector existed before this phase):

  * Each file has a one-line preamble (a title or footnote) before the real header row.
  * Weeks are MMWR weeks (Sunday-Saturday), not ISO weeks -- see app.ingestion.mmwr. Reusing
    FluNet's ISO-week logic would silently shift every date.
  * "X" marks a missing value. It is missing, not zero -- this also applies to the REGION column,
    where CDC uses the literal string "X" as a placeholder for national-level rows; that is a
    labelling convention, not a missing value, and is handled separately.
  * The latest week in any file is provisional and gets revised later.
  * Files supplied are National region-type only; other region types are skipped (out of scope),
    not rejected, since their indicator semantics (e.g. HHS region, state) are not verified here.
  * ILINet (outpatient illness) and NREVSS (laboratory positivity) measure different things and
    are stored as separate indicators; they are never combined into one series.

Indicator design decisions (stated plainly, not hidden in code):

  * ILINet's "% WEIGHTED ILI" is stored as the primary indicator (`ili_pct_weighted`,
    clinical_surveillance). ILITOTAL/TOTAL PATIENTS is recomputed and compared against
    "%UNWEIGHTED ILI" as a sanity check (same spirit as FluNet's SPEC_PROCESSED_NB cross-check);
    a mismatch beyond a small tolerance is flagged, never silently corrected.
  * NREVSS clinical labs' "PERCENT POSITIVE" is stored as `influenza_positivity_clinical`
    (laboratory_positivity), numerator = TOTAL A + TOTAL B, denominator = TOTAL SPECIMENS.
  * NREVSS public-health labs has no pre-computed percent-positive column. We sum all subtype/
    lineage count columns (A (2009 H1N1), A (H3), A (Subtyping not Performed), A (H5), H3N2v, B,
    BVic, BYam) as the positive count and store one aggregate `influenza_positivity_public_health`
    observation (laboratory_positivity); the individual subtype counts are preserved losslessly in
    `metadata` rather than invented as separate, unverified indicator slugs.
  * Neither NREVSS file's lab type maps onto WHO's sentinel/nonsentinel distinction, so
    `surveillance_stream` is left "undefined" and the CDC lab type is recorded in metadata instead
    of forcing it into a WHO-shaped category it does not fit.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from app.ingestion.countries import SUPPORTED_COUNTRIES
from app.ingestion.errors import SchemaMismatchError
from app.ingestion.mmwr import InvalidMMWRWeek, mmwr_week_end, mmwr_week_start
from app.ingestion.models import ObservationRecord, ParseResult, RowRejection

PARSER_VERSION = "cdc-fluview-1"

COUNTRY_ISO3 = "USA"  # key into SUPPORTED_COUNTRIES / IngestRequest.countries
COUNTRY_CODE = SUPPORTED_COUNTRIES[COUNTRY_ISO3].iso2  # "US" -- ObservationRecord.country_code
# is ISO 3166-1 alpha-2, not alpha-3; see app.ingestion.connectors.flunet_parser for the same
# convention.

FLAG_DERIVED = "derived_by_viruswatch"
FLAG_WEIGHTED_UNWEIGHTED_MISMATCH = "weighted_unweighted_ili_mismatch"
FLAG_NO_SPECIMENS = "no_specimens_reported"

_PCT_TOLERANCE = Decimal("1.5")  # percentage points; weighting legitimately shifts National rows
_PCT = Decimal("0.0001")


class _RowError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _clip(value: str, n: int = 60) -> str:
    return value if len(value) <= n else value[: n - 1] + "…"


def _split_preamble_and_header(content: bytes, *, header_marker: str) -> tuple[list[str], str]:
    """CDC FluView files start with one free-text line before the real header. Returns
    (data_lines_including_header, the preamble text or "")."""
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise SchemaMismatchError("The file is not valid UTF-8 text.") from exc
    lines = text.splitlines()
    if not lines:
        raise SchemaMismatchError("The file is empty.")
    if header_marker in lines[0].upper():
        return lines, ""
    if len(lines) > 1 and header_marker in lines[1].upper():
        return lines[1:], lines[0]
    raise SchemaMismatchError(
        f"Could not find a header row containing {header_marker!r} in the first two lines."
    )


def _reader_from_lines(lines: list[str]) -> csv.DictReader:
    return csv.DictReader(io.StringIO("\n".join(lines)))


def _parse_decimal(raw: str | None, column: str, *, allow_missing: bool = True) -> Decimal | None:
    if raw is None:
        raw = ""
    raw = raw.strip()
    if raw == "" or raw.upper() == "X":
        if allow_missing:
            return None
        raise _RowError("missing_required_value", f"{column} is missing")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise _RowError("invalid_number", f"{column}={_clip(raw)!r} is not a number") from None
    if not value.is_finite() or value < 0:
        raise _RowError("invalid_number", f"{column}={_clip(raw)!r} must be a non-negative number")
    return value


def _parse_mmwr(year_raw: str | None, week_raw: str | None):
    try:
        year, week = int((year_raw or "").strip()), int((week_raw or "").strip())
    except ValueError:
        raise _RowError(
            "invalid_mmwr_week", f"YEAR={_clip(year_raw or '')!r} WEEK={_clip(week_raw or '')!r}"
        ) from None
    try:
        return mmwr_week_start(year, week), mmwr_week_end(year, week)
    except InvalidMMWRWeek as exc:
        raise _RowError("invalid_mmwr_week", str(exc)) from None


@dataclass
class _Base:
    result: ParseResult


def _is_in_scope_row(
    row: dict[str, str | None], result: ParseResult, *, countries: frozenset[str]
) -> bool:
    region_type = (row.get("REGION TYPE") or "").strip().lower()
    if region_type != "national" or COUNTRY_CODE not in countries:
        result.rows_out_of_scope += 1
        return False
    return True


# --------------------------------------------------------------------------------------- ILINet


REQUIRED_ILINET_COLUMNS = (
    "REGION TYPE",
    "REGION",
    "YEAR",
    "WEEK",
    "% WEIGHTED ILI",
    "%UNWEIGHTED ILI",
    "ILITOTAL",
    "TOTAL PATIENTS",
)


def parse_ilinet_csv(content: bytes, *, source_url: str, countries: frozenset[str]) -> ParseResult:
    lines, preamble = _split_preamble_and_header(content, header_marker="REGION TYPE")
    reader = _reader_from_lines(lines)
    columns = tuple(reader.fieldnames or ())
    missing = [c for c in REQUIRED_ILINET_COLUMNS if c not in columns]
    if missing:
        raise SchemaMismatchError(f"ILINet.csv is missing column(s): {', '.join(missing)}.")

    result = ParseResult(columns=columns)
    if preamble:
        result.warnings.append(f"preamble line present: {_clip(preamble, 120)!r}")

    for row_number, row in enumerate(reader, start=2):  # header is line 1
        result.rows_read += 1
        if not _is_in_scope_row(row, result, countries=countries):
            continue
        try:
            start, end = _parse_mmwr(row.get("YEAR"), row.get("WEEK"))
            weighted = _parse_decimal(row.get("% WEIGHTED ILI"), "% WEIGHTED ILI")
            unweighted = _parse_decimal(row.get("%UNWEIGHTED ILI"), "%UNWEIGHTED ILI")
            ili_total = _parse_decimal(row.get("ILITOTAL"), "ILITOTAL")
            total_patients = _parse_decimal(row.get("TOTAL PATIENTS"), "TOTAL PATIENTS")
        except _RowError as err:
            result.rejections.append(RowRejection(row_number, err.reason, err.detail))
            continue

        flags: list[str] = []
        if weighted is not None and unweighted is not None:
            if abs(weighted - unweighted) > _PCT_TOLERANCE:
                flags.append(FLAG_WEIGHTED_UNWEIGHTED_MISMATCH)

        value = weighted
        data_status = "missing" if value is None else "provisional"
        # CDC revises the most recent 1-2 weeks of every release; without a "final" marker in the
        # file itself we cannot distinguish a settled week from a fresh one, so every row from
        # this source is conservatively marked provisional rather than asserting finality we
        # cannot verify (see docs/source_notes_cdc_fluview.md).
        result.observations.append(
            ObservationRecord(
                country_code=COUNTRY_CODE,
                disease="influenza",
                indicator="ili_pct_weighted",
                indicator_type="clinical_surveillance",
                surveillance_stream="sentinel",
                reporting_period_start=start,
                reporting_period_end=end,
                value_numeric=value,
                numerator=ili_total if value is not None else None,
                denominator=total_patients if value is not None else None,
                unit="percent",
                data_status=data_status,
                quality_flags=tuple(flags),
                source_record_id=f"cdc_ilinet:{start.isoformat()}",
                source_url=source_url,
                metadata={
                    "pct_unweighted_ili": str(unweighted) if unweighted is not None else None
                },
            )
        )
    return result


# ------------------------------------------------------------------------------ NREVSS clinical


REQUIRED_NREVSS_CLINICAL_COLUMNS = (
    "REGION TYPE",
    "REGION",
    "YEAR",
    "WEEK",
    "TOTAL SPECIMENS",
    "TOTAL A",
    "TOTAL B",
    "PERCENT POSITIVE",
)


def parse_nrevss_clinical_csv(
    content: bytes, *, source_url: str, countries: frozenset[str]
) -> ParseResult:
    lines, preamble = _split_preamble_and_header(content, header_marker="REGION TYPE")
    reader = _reader_from_lines(lines)
    columns = tuple(reader.fieldnames or ())
    missing = [c for c in REQUIRED_NREVSS_CLINICAL_COLUMNS if c not in columns]
    if missing:
        raise SchemaMismatchError(
            f"ICL_NREVSS_Clinical_Labs.csv is missing column(s): {', '.join(missing)}."
        )

    result = ParseResult(columns=columns)
    if preamble:
        result.warnings.append(f"preamble line present: {_clip(preamble, 120)!r}")

    for row_number, row in enumerate(reader, start=2):
        result.rows_read += 1
        if not _is_in_scope_row(row, result, countries=countries):
            continue
        try:
            start, end = _parse_mmwr(row.get("YEAR"), row.get("WEEK"))
            total_specimens = _parse_decimal(row.get("TOTAL SPECIMENS"), "TOTAL SPECIMENS")
            total_a = _parse_decimal(row.get("TOTAL A"), "TOTAL A")
            total_b = _parse_decimal(row.get("TOTAL B"), "TOTAL B")
        except _RowError as err:
            result.rejections.append(RowRejection(row_number, err.reason, err.detail))
            continue

        flags: list[str] = [FLAG_DERIVED]
        value: Decimal | None = None
        numerator: Decimal | None = None
        data_status = "provisional"
        if total_specimens is None:
            data_status = "missing"
        elif total_specimens == 0:
            flags.append(FLAG_NO_SPECIMENS)
            data_status = "missing"
        elif total_a is None or total_b is None:
            data_status = "missing"
        else:
            numerator = total_a + total_b
            if numerator > total_specimens:
                result.rejections.append(
                    RowRejection(
                        row_number,
                        "numerator_exceeds_denominator",
                        f"TOTAL A + TOTAL B ({numerator}) > TOTAL SPECIMENS ({total_specimens})",
                    )
                )
                continue
            value = (numerator / total_specimens * 100).quantize(_PCT, rounding=ROUND_HALF_EVEN)

        result.observations.append(
            ObservationRecord(
                country_code=COUNTRY_CODE,
                disease="influenza",
                indicator="influenza_positivity_clinical",
                indicator_type="laboratory_positivity",
                surveillance_stream="undefined",
                reporting_period_start=start,
                reporting_period_end=end,
                value_numeric=value,
                numerator=numerator,
                denominator=total_specimens if value is not None else None,
                unit="percent",
                data_status=data_status,
                quality_flags=tuple(flags),
                source_record_id=f"cdc_nrevss_clinical:{start.isoformat()}",
                source_url=source_url,
                metadata={"cdc_lab_type": "clinical"},
            )
        )
    return result


# ------------------------------------------------------------------------- NREVSS public health


_SUBTYPE_COLUMNS = (
    "A (2009 H1N1)",
    "A (H3)",
    "A (Subtyping not Performed)",
    "B",
    "BVic",
    "BYam",
    "H3N2v",
    "A (H5)",
)
REQUIRED_NREVSS_PUBLIC_HEALTH_COLUMNS = (
    "REGION TYPE",
    "REGION",
    "YEAR",
    "WEEK",
    "TOTAL SPECIMENS",
    *_SUBTYPE_COLUMNS,
)


def parse_nrevss_public_health_csv(
    content: bytes, *, source_url: str, countries: frozenset[str]
) -> ParseResult:
    lines, preamble = _split_preamble_and_header(content, header_marker="REGION TYPE")
    reader = _reader_from_lines(lines)
    columns = tuple(reader.fieldnames or ())
    missing = [c for c in REQUIRED_NREVSS_PUBLIC_HEALTH_COLUMNS if c not in columns]
    if missing:
        raise SchemaMismatchError(
            f"ICL_NREVSS_Public_Health_Labs.csv is missing column(s): {', '.join(missing)}."
        )

    result = ParseResult(columns=columns)
    if preamble:
        result.warnings.append(f"preamble line present: {_clip(preamble, 120)!r}")

    for row_number, row in enumerate(reader, start=2):
        result.rows_read += 1
        if not _is_in_scope_row(row, result, countries=countries):
            continue
        try:
            start, end = _parse_mmwr(row.get("YEAR"), row.get("WEEK"))
            total_specimens = _parse_decimal(row.get("TOTAL SPECIMENS"), "TOTAL SPECIMENS")
            subtypes = {col: _parse_decimal(row.get(col), col) for col in _SUBTYPE_COLUMNS}
        except _RowError as err:
            result.rejections.append(RowRejection(row_number, err.reason, err.detail))
            continue

        flags: list[str] = [FLAG_DERIVED]
        value: Decimal | None = None
        numerator: Decimal | None = None
        data_status = "provisional"
        present_subtypes = {k: v for k, v in subtypes.items() if v is not None}
        if total_specimens is None:
            data_status = "missing"
        elif total_specimens == 0:
            flags.append(FLAG_NO_SPECIMENS)
            data_status = "missing"
        elif len(present_subtypes) < len(subtypes):
            # A blank subtype column means "not reported", not zero; summing partial data would
            # understate positives, so this row's aggregate is not produced.
            result.warnings.append(
                f"row {row_number}: one or more subtype columns missing; aggregate not computed"
            )
            data_status = "missing"
        else:
            numerator = sum(present_subtypes.values())
            if numerator > total_specimens:
                result.rejections.append(
                    RowRejection(
                        row_number,
                        "numerator_exceeds_denominator",
                        f"sum of subtypes ({numerator}) > TOTAL SPECIMENS ({total_specimens})",
                    )
                )
                continue
            value = (numerator / total_specimens * 100).quantize(_PCT, rounding=ROUND_HALF_EVEN)

        result.observations.append(
            ObservationRecord(
                country_code=COUNTRY_CODE,
                disease="influenza",
                indicator="influenza_positivity_public_health",
                indicator_type="laboratory_positivity",
                surveillance_stream="undefined",
                reporting_period_start=start,
                reporting_period_end=end,
                value_numeric=value,
                numerator=numerator,
                denominator=total_specimens if value is not None else None,
                unit="percent",
                data_status=data_status,
                quality_flags=tuple(flags),
                source_record_id=f"cdc_nrevss_public_health:{start.isoformat()}",
                source_url=source_url,
                metadata={
                    "cdc_lab_type": "public_health",
                    **{k: (str(v) if v is not None else None) for k, v in subtypes.items()},
                },
            )
        )
    return result
