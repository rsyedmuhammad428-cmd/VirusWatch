"""Pure parser for the KP district-week notifiable infectious disease surveillance panel
(Phase 7; data_sources.slug = 'pk_kp_idsr_district_panel').

Source: a pre-compiled, already-tidy long-format CSV (`idsr_kp_long.csv`) derived from Table 4 of
164 weekly NIH Pakistan IDSR Public Health Bulletins for Khyber Pakhtunkhwa province. See
data/raw/phase7_newdata/District-week_notifiable_infectious_disease_survei.zip's own README.md
for the full provenance, district crosswalk and known-issue documentation (also summarized in
data/provenance/manifest.json's kp_idsr_district_week_2023_2026 entry and migration
20261102000011). This parser trusts that upstream cleaning: it does not re-derive the district
crosswalk or re-detect transcription errors, both already resolved in the source file's own
`district` (canonical) and `cases` (corrected) columns.

Normalisation rules:

* One row here = one (district-or-subdivision, ISO week, condition). Conditions are syndromic/
  clinical notifications, not lab-confirmed diagnoses, so every observation is
  indicator_type='clinical_surveillance', pathogen=None.
* district -> geography is resolved at persistence time by
  (geography_code_system='kp_idsr_district_crosswalk', source_geography_code=district), matching
  the geography rows migration 20261102000011 seeds with that exact code_system and the district/
  sub-division name as source_geography_code. unit_type ('district'/'subdivision'/'typo') is
  informational only -- the source file's `district` column is already the canonical unit either
  way, so it alone decides which geography row this resolves to.
* epi_week is treated as an ISO week (Monday start), the same convention used for WHO FluNet in
  this codebase. This has not been independently verified against NIH Pakistan's own epidemiological
  calendar (which some national IDSR systems define as Sunday-start); if that turns out to differ,
  only the reporting_period_start/end dates would need correcting, not the case counts themselves.
* A blank `cases` cell means "not reported" (data_status='missing', no value) -- it is never
  converted to a 0. Absence of a (district, week, condition) row entirely is a *different* thing:
  per the source README, 10 of 16 conditions are right-censored to the province's top 10 by count
  from 2023 week 32 onward, so an absent row there means "did not rank that week", not "zero
  cases" and not "not reported". This parser cannot and does not distinguish those two kinds of
  absence from the long-format file alone (both simply produce no row); it is documented here so
  the distinction is not silently lost down the line. See the source README's "Three things to
  know before using this data" section for the full explanation.
* condition -> disease mapping is deliberately disease-specific, not generic "notifiable_disease",
  so this data joins the same `disease` identifier space as every other connector (Prompt.txt
  prompt_3: the five priority diseases dengue/malaria/measles/influenza(-like)/COVID-19 use the
  same identifiers used elsewhere -- dengue, malaria, measles, influenza; this source has no
  COVID-19 condition). ILI and SARI are both mapped to disease='influenza' (two separate
  indicators, since they are two separate case definitions: influenza-like illness is an
  outpatient syndrome, severe acute respiratory infection an inpatient one), never summed.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.ingestion.errors import SchemaMismatchError
from app.ingestion.models import ObservationRecord, ParseResult, RowRejection

PARSER_VERSION = "kp_idsr-1"
GEOGRAPHY_CODE_SYSTEM = "kp_idsr_district_crosswalk"

REQUIRED_COLUMNS = (
    "year",
    "epi_week",
    "district",
    "unit_type",
    "condition",
    "cases",
)

# condition (idsr_kp_long.csv's canonical code) -> (disease, indicator). All clinical_surveillance,
# all pathogen=None -- these are case notifications, not laboratory results.
CONDITION_MAP: dict[str, tuple[str, str]] = {
    "AD_noncholera": ("acute_diarrhoea_non_cholera", "suspected_case_count"),
    "ALRI_u5": ("acute_lower_respiratory_infection", "suspected_case_count_under5"),
    "AVH": ("acute_viral_hepatitis", "suspected_case_count"),
    "AWD_cholera": ("acute_watery_diarrhoea_cholera", "suspected_case_count"),
    "Bloody_diarrhoea": ("bloody_diarrhoea", "suspected_case_count"),
    "Chickenpox": ("chickenpox", "suspected_case_count"),
    "Cut_leishmaniasis": ("cutaneous_leishmaniasis", "suspected_case_count"),
    "Dengue": ("dengue", "suspected_case_count"),
    "Dog_bite": ("animal_bite", "suspected_case_count"),
    "ILI": ("influenza", "ili_case_count"),
    "Malaria": ("malaria", "suspected_case_count"),
    "Measles": ("measles", "suspected_case_count"),
    "SARI": ("influenza", "sari_case_count"),
    "TB": ("tuberculosis", "suspected_case_count"),
    "Typhoid": ("typhoid", "suspected_case_count"),
    "VH_BCD": ("viral_hepatitis_b_c_d", "suspected_case_count"),
}


@dataclass(frozen=True)
class _RowError(Exception):
    reason: str
    detail: str = ""


def _week_bounds(year_raw: str, week_raw: str) -> tuple[date, date]:
    try:
        year, week = int(year_raw), int(week_raw)
        start = date.fromisocalendar(year, week, 1)
    except (ValueError, TypeError):
        raise _RowError("invalid_iso_week", f"{year_raw!r}-W{week_raw!r}") from None
    return start, start + timedelta(days=6)


def _value(cases_raw: str) -> tuple[Decimal | None, str]:
    """Returns (value_numeric, data_status). A blank cell is 'missing', never a 0."""
    raw = (cases_raw or "").strip()
    if not raw:
        return None, "missing"
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise _RowError("non_numeric_cases", raw) from None
    if value < 0 or value != value.to_integral_value():
        raise _RowError("non_numeric_cases", raw)
    return value, "reported"


def parse_kp_idsr_csv(content: bytes, *, source_url: str) -> ParseResult:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    header = tuple(reader.fieldnames or ())
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise SchemaMismatchError(
            f"kp_idsr CSV is missing required column(s): {', '.join(missing)}"
        )

    result = ParseResult(columns=header)
    for row_number, row in enumerate(reader, start=2):  # header is line 1
        result.rows_read += 1
        district = (row.get("district") or "").strip()
        condition = (row.get("condition") or "").strip()
        try:
            if not district:
                raise _RowError("missing_district")
            mapped = CONDITION_MAP.get(condition)
            if mapped is None:
                raise _RowError("unknown_condition", condition)
            disease, indicator = mapped
            start, end = _week_bounds(row.get("year", ""), row.get("epi_week", ""))
            value, data_status = _value(row.get("cases", ""))
            observation = ObservationRecord(
                country_code="PK",
                geography_code_system=GEOGRAPHY_CODE_SYSTEM,
                source_geography_code=district,
                disease=disease,
                pathogen=None,
                indicator=indicator,
                indicator_type="clinical_surveillance",
                reporting_period_start=start,
                reporting_period_end=end,
                value_numeric=value,
                unit="cases" if value is not None else None,
                data_status=data_status,
                source_record_id=f"{start.isocalendar()[0]}-W{start.isocalendar()[1]:02d}:"
                f"{district}:{condition}",
                source_url=source_url,
                metadata={
                    "parser_version": PARSER_VERSION,
                    "district_as_printed": row.get("district_as_printed"),
                    "unit_type": row.get("unit_type"),
                    "condition_as_printed": row.get("condition_as_printed"),
                },
            )
        except _RowError as exc:
            result.rejections.append(RowRejection(row_number, exc.reason, exc.detail))
            continue
        except ValueError as exc:  # pydantic ValidationError and friends
            result.rejections.append(RowRejection(row_number, "invalid_record", str(exc)[:200]))
            continue
        result.observations.append(observation)
    return result
