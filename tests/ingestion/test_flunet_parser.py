from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ingestion.connectors import flunet_parser as fp
from app.ingestion.connectors.flunet_parser import parse_flunet_csv
from app.ingestion.errors import IngestionError, SchemaMismatchError
from tests.ingestion.flunet_helpers import FIXTURE, HEADER, csv_bytes, row

URL = "https://example.test/flunet?$format=csv"
PAK = frozenset({"PAK"})
PAK_USA = frozenset({"PAK", "USA"})


def parse(content: bytes, countries=PAK, since_year=None):
    return parse_flunet_csv(content, countries=countries, since_year=since_year, source_url=URL)


def by_indicator(result, indicator, pathogen=None, stream="sentinel", week_start=date(2024, 1, 1)):
    matches = [
        o
        for o in result.observations
        if o.indicator == indicator
        and o.pathogen == pathogen
        and o.surveillance_stream == stream
        and o.reporting_period_start == week_start
    ]
    assert len(matches) == 1, f"expected one {indicator}/{pathogen}, got {len(matches)}"
    return matches[0]


# ------------------------------------------------------------------ header validation
class TestHeader:
    def test_missing_required_column_fails_loudly(self):
        bad = HEADER.replace("INF_NEGATIVE", "SOMETHING_ELSE")
        with pytest.raises(SchemaMismatchError) as exc:
            parse(csv_bytes(row(), header=bad))
        assert "INF_NEGATIVE" in str(exc.value)
        assert "VIW_FLU_METADATA" in str(exc.value)  # tells the operator where to look

    def test_empty_file(self):
        with pytest.raises(SchemaMismatchError):
            parse(b"")

    def test_duplicate_columns_rejected(self):
        with pytest.raises(SchemaMismatchError):
            parse(csv_bytes(row(), header=HEADER + ",INF_ALL"))

    def test_invalid_utf8(self):
        with pytest.raises(SchemaMismatchError):
            parse(b"COUNTRY_CODE,ISO_YEAR\n\xff\xfe\n")

    def test_bom_and_case_and_whitespace_tolerated(self):
        header = "﻿" + HEADER.lower().replace(",", " , ")
        result = parse(("\n".join([header, row()]) + "\n").encode("utf-8"))
        assert result.rows_rejected == 0 and result.observations

    def test_unsupported_country_is_a_programming_error(self):
        with pytest.raises(IngestionError):
            parse(csv_bytes(row()), countries=frozenset({"XXX"}))

    def test_absent_optional_columns_produce_warnings_not_errors(self):
        header = "COUNTRY_CODE,ISO_YEAR,ISO_WEEK,ORIGIN_SOURCE,INF_ALL,INF_NEGATIVE"
        result = parse(b"\n".join([header.encode(), b"PAK,2024,1,SENTINEL,5,45"]) + b"\n")
        indicators = {o.indicator for o in result.observations}
        assert indicators == {"influenza_positive_count", "influenza_positivity"}
        assert any("SPEC_PROCESSED_NB" in w for w in result.warnings)


# ------------------------------------------------------------------ values and derivations
class TestValues:
    def test_positivity_is_derived_with_provenance(self):
        r = parse(csv_bytes(row(INF_ALL=25, INF_NEGATIVE=70, SPEC_PROCESSED_NB=95)))
        pos = by_indicator(r, "influenza_positivity")
        assert pos.value_numeric == Decimal("26.3158")  # 25 / 95 * 100, 4 dp, half-even
        assert (pos.numerator, pos.denominator) == (25, 95)
        assert pos.unit == "percent" and pos.indicator_type == "laboratory_positivity"
        assert pos.quality_flags == (fp.FLAG_DERIVED,)
        assert pos.metadata["source_columns"] == ["INF_ALL", "INF_NEGATIVE"]

    def test_rounding_is_half_even_not_float_artifacts(self):
        # 1/8 = 12.5 exactly; 1/16 = 6.25; 1/32 = 3.125; 1/64 = 1.5625 (representable)
        r = parse(csv_bytes(row(INF_ALL=1, INF_NEGATIVE=63)))
        assert by_indicator(r, "influenza_positivity").value_numeric == Decimal("1.5625")

    def test_denominator_differing_from_specimens_is_flagged_not_fixed(self):
        r = parse(csv_bytes(row(SPEC_PROCESSED_NB=100, INF_ALL=25, INF_NEGATIVE=70)))
        pos = by_indicator(r, "influenza_positivity")
        assert fp.FLAG_DENOMINATOR_DIFFERS in pos.quality_flags
        assert pos.denominator == 95  # still the tested count
        assert by_indicator(r, "specimens_processed").value_numeric == 100

    def test_matching_specimens_are_not_flagged(self):
        r = parse(csv_bytes(row(SPEC_PROCESSED_NB=50, INF_ALL=5, INF_NEGATIVE=45)))
        assert (
            fp.FLAG_DENOMINATOR_DIFFERS not in by_indicator(r, "influenza_positivity").quality_flags
        )

    def test_small_denominator_flagged(self):
        r = parse(csv_bytes(row(INF_ALL=3, INF_NEGATIVE=4)))
        assert fp.FLAG_SMALL_DENOMINATOR in by_indicator(r, "influenza_positivity").quality_flags

    def test_blank_values_are_missing_never_zero(self):
        r = parse(csv_bytes(row(INF_ALL=None, INF_NEGATIVE=None, SPEC_PROCESSED_NB=None)))
        for indicator in (
            "influenza_positive_count",
            "influenza_positivity",
            "specimens_processed",
        ):
            o = by_indicator(r, indicator)
            assert o.data_status == "missing" and o.value_numeric is None, indicator
        assert fp.FLAG_INPUT_MISSING in by_indicator(r, "influenza_positivity").quality_flags

    def test_one_blank_input_makes_positivity_missing_but_keeps_the_count(self):
        r = parse(csv_bytes(row(INF_ALL=5, INF_NEGATIVE=None)))
        assert by_indicator(r, "influenza_positivity").data_status == "missing"
        assert by_indicator(r, "influenza_positive_count").value_numeric == 5

    def test_zero_tests_means_undefined_positivity_but_real_zero_counts(self):
        r = parse(csv_bytes(row(INF_ALL=0, INF_NEGATIVE=0, SPEC_PROCESSED_NB=0)))
        pos = by_indicator(r, "influenza_positivity")
        assert pos.data_status == "missing" and fp.FLAG_NO_TESTS in pos.quality_flags
        assert pos.denominator == 0
        count = by_indicator(r, "influenza_positive_count")
        assert count.data_status == "reported" and count.value_numeric == 0

    def test_subtype_counts_omitted_when_blank_but_zero_is_kept(self):
        r = parse(csv_bytes(row(AH3=0, AH1N12009=None, INF_A=7)))
        pathogens = {o.pathogen for o in r.observations if o.pathogen}
        assert pathogens == {"influenza_a_h3n2", "influenza_a"}
        assert by_indicator(r, "influenza_positive_count", "influenza_a_h3n2").value_numeric == 0

    def test_victoria_summed_only_when_all_four_components_present(self):
        full = parse(csv_bytes(row(BVIC_2DEL=1, BVIC_3DEL=2, BVIC_NODEL=3, BVIC_DELUNK=0)))
        assert (
            by_indicator(full, "influenza_positive_count", "influenza_b_victoria").value_numeric
            == 6
        )
        partial = parse(csv_bytes(row(BVIC_2DEL=1, BVIC_3DEL=2, BVIC_NODEL=3)))
        assert not [o for o in partial.observations if o.pathogen == "influenza_b_victoria"]

    def test_component_sum_mismatch_flagged_and_kept(self):
        r = parse(csv_bytes(row(INF_A=10, INF_B=5, INF_ALL=20, INF_NEGATIVE=20)))
        assert (
            fp.FLAG_COMPONENT_MISMATCH in by_indicator(r, "influenza_positive_count").quality_flags
        )

    @pytest.mark.parametrize("raw", ["12.0", " 12 ", "1E1"])
    def test_whole_numbers_in_other_notations_accepted(self, raw):
        r = parse(csv_bytes(row(INF_ALL=raw, INF_NEGATIVE=0)))
        assert not r.rejections
        assert by_indicator(r, "influenza_positive_count").value_numeric in (10, 12)

    # '"1,000"' is a properly quoted thousands separator (unquoted it would be an extra CSV field).
    @pytest.mark.parametrize("raw", ["12.5", "-1", "abc", "NaN", "Infinity", '"1,000"'])
    def test_non_count_values_reject_the_row(self, raw):
        r = parse(csv_bytes(row(INF_ALL=raw)))
        assert [x.reason for x in r.rejections] == ["invalid_count"]
        assert r.observations == []


# ------------------------------------------------------------------ periods and streams
class TestPeriodsAndStreams:
    def test_period_is_the_iso_week_monday_to_sunday(self):
        o = by_indicator(parse(csv_bytes(row())), "influenza_positivity")
        assert (o.reporting_period_start, o.reporting_period_end) == (
            date(2024, 1, 1),
            date(2024, 1, 7),
        )

    def test_week_53_and_year_boundary(self):
        r = parse(csv_bytes(row(ISO_YEAR=2020, ISO_WEEK=53, ISO_WEEKSTARTDATE="2020-12-28")))
        o = r.observations[0]
        assert (o.reporting_period_start, o.reporting_period_end) == (
            date(2020, 12, 28),
            date(2021, 1, 3),
        )

    def test_week_53_in_a_52_week_year_rejected(self):
        r = parse(csv_bytes(row(ISO_YEAR=2024, ISO_WEEK=53, ISO_WEEKSTARTDATE=None)))
        assert [x.reason for x in r.rejections] == ["invalid_iso_week"]

    @pytest.mark.parametrize(
        "year,week", [("abc", "1"), ("2024", ""), ("2024", "0"), ("2024", "54")]
    )
    def test_bad_year_or_week_rejected(self, year, week):
        r = parse(csv_bytes(row(ISO_YEAR=year, ISO_WEEK=week, ISO_WEEKSTARTDATE=None)))
        assert [x.reason for x in r.rejections] == ["invalid_iso_week"]

    def test_week_start_disagreement_rejected(self):
        r = parse(csv_bytes(row(ISO_WEEKSTARTDATE="2024-01-02")))
        assert [x.reason for x in r.rejections] == ["week_start_mismatch"]

    def test_week_start_with_time_component_accepted(self):
        r = parse(csv_bytes(row(ISO_WEEKSTARTDATE="2024-01-01T00:00:00")))
        assert not r.rejections

    def test_unparseable_week_start_warns_once_and_does_not_reject(self):
        r = parse(
            csv_bytes(
                row(ISO_WEEKSTARTDATE="01/01/24"), row(ISO_WEEK=2, ISO_WEEKSTARTDATE="08/01/24")
            )
        )
        assert not r.rejections
        assert len([w for w in r.warnings if "unrecognised date format" in w]) == 1

    def test_streams_are_separate_series(self):
        r = parse(
            csv_bytes(
                row(ORIGIN_SOURCE="SENTINEL", INF_ALL=1),
                row(ORIGIN_SOURCE="NONSENTINEL", INF_ALL=2),
                row(ORIGIN_SOURCE="NOTDEFINED", INF_ALL=3),
            )
        )
        counts = {
            o.surveillance_stream: o.value_numeric
            for o in r.observations
            if o.indicator == "influenza_positive_count" and o.pathogen is None
        }
        assert counts == {"sentinel": 1, "nonsentinel": 2, "undefined": 3}

    def test_unknown_stream_rejected(self):
        r = parse(csv_bytes(row(ORIGIN_SOURCE="MYSTERY")))
        assert [x.reason for x in r.rejections] == ["unknown_stream"]


# ------------------------------------------------------------------ scope and duplicates
class TestScopeAndDuplicates:
    def test_other_countries_are_skipped_without_validation(self):
        r = parse(csv_bytes(row(COUNTRY_CODE="IND", INF_ALL="garbage"), row()))
        assert r.rows_read == 2 and r.rows_out_of_scope == 1 and not r.rejections

    def test_since_year_skips_earlier_rows(self):
        r = parse(
            csv_bytes(row(ISO_YEAR=2023, ISO_WEEK=52, ISO_WEEKSTARTDATE="2023-12-25"), row()),
            since_year=2024,
        )
        assert r.rows_out_of_scope == 1
        assert {o.reporting_period_start.year for o in r.observations} == {2024}

    def test_identical_duplicate_rows_collapse_to_one(self):
        r = parse(csv_bytes(row(), row()))
        assert r.rows_duplicate_identical == 1 and not r.rejections
        assert len([o for o in r.observations if o.indicator == "influenza_positivity"]) == 1

    def test_conflicting_duplicates_reject_both_rows(self):
        r = parse(csv_bytes(row(INF_ALL=5), row(INF_ALL=6)))
        assert [x.reason for x in r.rejections] == ["conflicting_duplicate"] * 2
        assert [x.row_number for x in r.rejections] == [2, 3]
        assert r.observations == []

    def test_short_row_rejected_as_malformed(self):
        r = parse(csv_bytes("PAK,2024,1"))
        assert [x.reason for x in r.rejections] == ["malformed_row"]

    def test_blank_lines_ignored(self):
        r = parse((HEADER + "\n\n" + row() + "\n\n").encode())
        assert r.rows_read == 1 and not r.rejections

    def test_row_numbers_refer_to_file_lines(self):
        r = parse(csv_bytes(row(), row(ISO_WEEK=2, ISO_WEEKSTARTDATE="2024-01-08", INF_ALL="x")))
        assert r.rejections[0].row_number == 3  # header is line 1

    def test_natural_keys_are_unique_across_a_result(self):
        r = parse(FIXTURE.read_bytes(), countries=PAK_USA)
        keys = [o.natural_key for o in r.observations]
        assert len(keys) == len(set(keys))


# ------------------------------------------------------------------ the synthetic fixture
class TestFixtureFile:
    def result(self, since_year=None):
        return parse(FIXTURE.read_bytes(), countries=PAK_USA, since_year=since_year)

    def test_row_accounting(self):
        r = self.result()
        assert r.rows_read == 18
        assert r.rows_out_of_scope == 1  # India
        assert r.rows_duplicate_identical == 1
        assert r.rows_rejected == 7
        assert r.reject_reason_counts() == {
            "conflicting_duplicate": 2,
            "invalid_count": 1,
            "invalid_iso_week": 1,
            "malformed_row": 1,
            "unknown_stream": 1,
            "week_start_mismatch": 1,
        }

    def test_observation_count_and_latest_period(self):
        r = self.result()
        assert len(r.observations) == 43  # 8+5+3+5+3+4+5+5+5 across the nine accepted weeks
        assert r.latest_period_end == date(2024, 3, 3)  # 2024-W09 ends Sunday 3 March
        assert {o.country_code for o in r.observations} == {"PK", "US"}

    def test_since_year_filters_the_fixture(self):
        r = self.result(since_year=2024)
        assert all(o.reporting_period_start.year >= 2024 for o in r.observations)
        assert r.rows_out_of_scope == 3  # India + 2023-W52 + 2020-W53

    def test_every_observation_names_its_source_and_never_fakes_values(self):
        for o in self.result().observations:
            assert o.source_url == URL and o.source_record_id.count("|") == 2
            assert (o.value_numeric is None) == (o.data_status == "missing")
