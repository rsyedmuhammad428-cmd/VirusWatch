from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.connectors.cdc_fluview_parser import (
    FLAG_NO_SPECIMENS,
    FLAG_WEIGHTED_UNWEIGHTED_MISMATCH,
    parse_ilinet_csv,
    parse_nrevss_clinical_csv,
    parse_nrevss_public_health_csv,
)
from app.ingestion.errors import SchemaMismatchError
from tests.ingestion.cdc_fluview_helpers import (
    ILINET_HEADER,
    NREVSS_CLINICAL_HEADER,
    ilinet_csv_bytes,
    ilinet_row,
    nrevss_clinical_csv_bytes,
    nrevss_clinical_row,
    nrevss_public_health_csv_bytes,
    nrevss_public_health_row,
)

URL = "https://example.test/fluview"
US = frozenset({"US"})

REAL_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "cdc_fluview"


# ----------------------------------------------------------------------------------------- ILINet


class TestIlinet:
    def test_happy_path(self):
        result = parse_ilinet_csv(ilinet_csv_bytes([ilinet_row()]), source_url=URL, countries=US)
        assert result.rows_rejected == 0
        assert len(result.observations) == 1
        obs = result.observations[0]
        assert obs.indicator == "ili_pct_weighted"
        assert obs.indicator_type == "clinical_surveillance"
        assert obs.surveillance_stream == "sentinel"
        assert obs.reporting_period_start == date(2023, 10, 1)  # MMWR 2023-W40
        assert obs.reporting_period_end == date(2023, 10, 7)
        assert obs.value_numeric == Decimal("2.27908")
        assert obs.numerator == Decimal("56309")
        assert obs.denominator == Decimal("2489991")
        assert obs.data_status == "provisional"
        assert obs.quality_flags == ()

    def test_missing_required_column_fails_loudly(self):
        bad_header = [c for c in ILINET_HEADER if c != "ILITOTAL"]
        from tests.ingestion.cdc_fluview_helpers import ILINET_PREAMBLE, csv_bytes

        content = csv_bytes(ILINET_PREAMBLE, bad_header, [{}])
        with pytest.raises(SchemaMismatchError) as exc:
            parse_ilinet_csv(content, source_url=URL, countries=US)
        assert "ILITOTAL" in str(exc.value)

    def test_empty_file_rejected(self):
        with pytest.raises(SchemaMismatchError):
            parse_ilinet_csv(b"", source_url=URL, countries=US)

    def test_non_national_region_is_out_of_scope_not_rejected(self):
        rows = [ilinet_row(**{"REGION TYPE": "HHS Regions", "REGION": "Region 1"})]
        result = parse_ilinet_csv(ilinet_csv_bytes(rows), source_url=URL, countries=US)
        assert result.rows_out_of_scope == 1
        assert result.observations == []
        assert result.rejections == []

    def test_country_not_requested_is_out_of_scope(self):
        result = parse_ilinet_csv(
            ilinet_csv_bytes([ilinet_row()]), source_url=URL, countries=frozenset({"PAK"})
        )
        assert result.rows_out_of_scope == 1
        assert result.observations == []

    def test_x_marks_missing_not_zero(self):
        rows = [ilinet_row(**{"% WEIGHTED ILI": "X", "ILITOTAL": "X"})]
        result = parse_ilinet_csv(ilinet_csv_bytes(rows), source_url=URL, countries=US)
        obs = result.observations[0]
        assert obs.value_numeric is None
        assert obs.data_status == "missing"
        assert obs.numerator is None  # never defaults a missing numerator to 0

    def test_weighted_unweighted_mismatch_flagged_not_corrected(self):
        rows = [ilinet_row(**{"% WEIGHTED ILI": "10.0", "%UNWEIGHTED ILI": "2.0"})]
        result = parse_ilinet_csv(ilinet_csv_bytes(rows), source_url=URL, countries=US)
        obs = result.observations[0]
        assert obs.value_numeric == Decimal("10.0")  # weighted value kept as-is
        assert FLAG_WEIGHTED_UNWEIGHTED_MISMATCH in obs.quality_flags

    def test_small_divergence_not_flagged(self):
        rows = [ilinet_row(**{"% WEIGHTED ILI": "2.5", "%UNWEIGHTED ILI": "2.4"})]
        result = parse_ilinet_csv(ilinet_csv_bytes(rows), source_url=URL, countries=US)
        assert result.observations[0].quality_flags == ()

    def test_invalid_mmwr_week_rejected(self):
        rows = [ilinet_row(WEEK="99")]
        result = parse_ilinet_csv(ilinet_csv_bytes(rows), source_url=URL, countries=US)
        assert result.rows_rejected == 1
        assert result.rejections[0].reason == "invalid_mmwr_week"

    def test_negative_number_rejected(self):
        rows = [ilinet_row(**{"ILITOTAL": "-5"})]
        result = parse_ilinet_csv(ilinet_csv_bytes(rows), source_url=URL, countries=US)
        assert result.rows_rejected == 1
        assert result.rejections[0].reason == "invalid_number"

    def test_preamble_recorded_as_warning(self):
        result = parse_ilinet_csv(ilinet_csv_bytes([ilinet_row()]), source_url=URL, countries=US)
        assert any("preamble" in w for w in result.warnings)


# --------------------------------------------------------------------------------- NREVSS clinical


class TestNrevssClinical:
    def test_happy_path_recomputes_percent_positive(self):
        result = parse_nrevss_clinical_csv(
            nrevss_clinical_csv_bytes([nrevss_clinical_row()]), source_url=URL, countries=US
        )
        obs = result.observations[0]
        assert obs.indicator == "influenza_positivity_clinical"
        assert obs.indicator_type == "laboratory_positivity"
        assert obs.surveillance_stream == "undefined"
        assert obs.numerator == Decimal("715")  # TOTAL A + TOTAL B = 400 + 315
        assert obs.denominator == Decimal("64939")
        # (715 / 64939) * 100, rounded half-even to 4 dp
        assert obs.value_numeric == Decimal("1.1010")
        assert obs.metadata["cdc_lab_type"] == "clinical"

    def test_zero_specimens_flagged_missing_not_zero_percent(self):
        rows = [nrevss_clinical_row(**{"TOTAL SPECIMENS": "0", "TOTAL A": "0", "TOTAL B": "0"})]
        result = parse_nrevss_clinical_csv(
            nrevss_clinical_csv_bytes(rows), source_url=URL, countries=US
        )
        obs = result.observations[0]
        assert obs.value_numeric is None
        assert obs.data_status == "missing"
        assert FLAG_NO_SPECIMENS in obs.quality_flags

    def test_numerator_exceeding_denominator_rejected(self):
        rows = [nrevss_clinical_row(**{"TOTAL SPECIMENS": "10", "TOTAL A": "8", "TOTAL B": "8"})]
        result = parse_nrevss_clinical_csv(
            nrevss_clinical_csv_bytes(rows), source_url=URL, countries=US
        )
        assert result.rows_rejected == 1
        assert result.rejections[0].reason == "numerator_exceeds_denominator"
        assert result.observations == []

    def test_missing_total_a_leaves_row_missing(self):
        rows = [nrevss_clinical_row(**{"TOTAL A": "X"})]
        result = parse_nrevss_clinical_csv(
            nrevss_clinical_csv_bytes(rows), source_url=URL, countries=US
        )
        obs = result.observations[0]
        assert obs.value_numeric is None
        assert obs.data_status == "missing"

    def test_missing_required_column(self):
        bad_header = [c for c in NREVSS_CLINICAL_HEADER if c != "TOTAL SPECIMENS"]
        from tests.ingestion.cdc_fluview_helpers import NREVSS_PREAMBLE, csv_bytes

        content = csv_bytes(NREVSS_PREAMBLE, bad_header, [{}])
        with pytest.raises(SchemaMismatchError):
            parse_nrevss_clinical_csv(content, source_url=URL, countries=US)


# ---------------------------------------------------------------------------- NREVSS public health


class TestNrevssPublicHealth:
    def test_happy_path_sums_subtypes(self):
        result = parse_nrevss_public_health_csv(
            nrevss_public_health_csv_bytes([nrevss_public_health_row()]),
            source_url=URL,
            countries=US,
        )
        obs = result.observations[0]
        assert obs.indicator == "influenza_positivity_public_health"
        # 176 + 16 + 10 + 3 + 48 + 0 + 0 + 0 = 253
        assert obs.numerator == Decimal("253")
        assert obs.denominator == Decimal("3064")
        assert obs.metadata["A (2009 H1N1)"] == "176"
        assert obs.metadata["cdc_lab_type"] == "public_health"

    def test_one_missing_subtype_column_blocks_aggregate(self):
        rows = [nrevss_public_health_row(**{"BVic": "X"})]
        result = parse_nrevss_public_health_csv(
            nrevss_public_health_csv_bytes(rows), source_url=URL, countries=US
        )
        obs = result.observations[0]
        assert obs.value_numeric is None
        assert obs.data_status == "missing"
        assert any("subtype columns missing" in w for w in result.warnings)

    def test_zero_specimens_flagged(self):
        rows = [
            nrevss_public_health_row(
                **{
                    "TOTAL SPECIMENS": "0",
                    "A (2009 H1N1)": "0",
                    "A (H3)": "0",
                    "A (Subtyping not Performed)": "0",
                    "B": "0",
                    "BVic": "0",
                    "BYam": "0",
                    "H3N2v": "0",
                    "A (H5)": "0",
                }
            )
        ]
        result = parse_nrevss_public_health_csv(
            nrevss_public_health_csv_bytes(rows), source_url=URL, countries=US
        )
        obs = result.observations[0]
        assert obs.data_status == "missing"
        assert FLAG_NO_SPECIMENS in obs.quality_flags


# ------------------------------------------------------------------------- against the real files


class TestAgainstRealSuppliedFiles:
    """The three CSVs the project owner actually supplied -- real CDC FluView releases, not
    synthetic fixtures. Regressions here mean the parser would choke on the real thing."""

    @pytest.mark.skipif(not REAL_FIXTURE_DIR.is_dir(), reason="real fixture files not present")
    def test_ilinet_real_file_zero_rejections(self):
        content = (REAL_FIXTURE_DIR / "ILINet.csv").read_bytes()
        result = parse_ilinet_csv(content, source_url=URL, countries=US)
        assert result.rows_rejected == 0
        assert len(result.observations) == result.rows_read

    @pytest.mark.skipif(not REAL_FIXTURE_DIR.is_dir(), reason="real fixture files not present")
    def test_nrevss_clinical_real_file_zero_rejections(self):
        content = (REAL_FIXTURE_DIR / "ICL_NREVSS_Clinical_Labs.csv").read_bytes()
        result = parse_nrevss_clinical_csv(content, source_url=URL, countries=US)
        assert result.rows_rejected == 0
        assert len(result.observations) == result.rows_read

    @pytest.mark.skipif(not REAL_FIXTURE_DIR.is_dir(), reason="real fixture files not present")
    def test_nrevss_public_health_real_file_zero_rejections(self):
        content = (REAL_FIXTURE_DIR / "ICL_NREVSS_Public_Health_Labs.csv").read_bytes()
        result = parse_nrevss_public_health_csv(content, source_url=URL, countries=US)
        assert result.rows_rejected == 0
        assert len(result.observations) == result.rows_read
