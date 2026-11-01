from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.surveillance import service as svc

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
WEEK_START = date(2026, 10, 12)  # a Monday
WEEK_END = date(2026, 10, 18)


def obs_row(**kw):
    base = {
        "observation_id": "o-1",
        "country_code": "PK",
        "country_name": "Pakistan",
        "geographic_resolution": "country",
        "geography_id": "g-pk",
        "disease": "influenza",
        "pathogen": None,
        "indicator": "influenza_positivity",
        "indicator_type": "laboratory_positivity",
        "surveillance_stream": "sentinel",
        "period_start": WEEK_START,
        "period_end": WEEK_END,
        "value": Decimal("12.5"),
        "unit": "percent",
        "numerator": Decimal("25"),
        "denominator": Decimal("200"),
        "data_status": "reported",
        "quality_flags": '["derived_by_viruswatch"]',
        "published_at": None,
        "retrieved_at": NOW,
        "source_url": "https://example.test/flunet",
        "source_slug": "who_flunet",
        "source_name": "WHO FluNet",
        "source_publisher": "World Health Organization",
        "license_name": None,
        "terms_verified": False,
    }
    return base | kw


def analysis_row(analysis_type="anomaly_detection", status="normal", **kw):
    base = {
        "analysis_id": "a-1",
        "country_code": "PK",
        "country_name": "Pakistan",
        "geographic_resolution": "country",
        "geography_id": "g-pk",
        "disease": "influenza",
        "pathogen": None,
        "indicator": "influenza_positivity",
        "surveillance_stream": "sentinel",
        "analysis_type": analysis_type,
        "status": status,
        "period_start": WEEK_START,
        "period_end": WEEK_END,
        "baseline_start": None,
        "baseline_end": None,
        "latest_observation_end": WEEK_END,
        "observed_value": Decimal("30"),
        "baseline_value": Decimal("10"),
        "effect_size": Decimal("20"),
        "score": Decimal("20"),
        "threshold": Decimal("3.5"),
        "uncertainty": json.dumps({"baseline_kind": "trailing"}),
        "methodology_version": "1.0.0",
        "missing_data_handling": "never zero",
        "limitations": "screening only",
        "input_count": 9,
        "explanation": "text",
        "computed_at": NOW,
    }
    return base | kw


# ------------------------------------------------------------------ geography honesty
class TestResolutionNotes:
    def test_national_data_is_never_described_as_local(self):
        note = svc.resolution_note("country")
        assert "National-level" in note and "must not be read as describing any city" in note

    def test_sub_national_notes_name_the_area(self):
        assert "'Sindh'" in svc.resolution_note("admin1", "Sindh")

    def test_every_series_carries_its_resolution_note(self):
        ref = svc.series_ref(obs_row())
        assert ref.geographic_resolution == "country" and "National-level" in ref.resolution_note

    def test_geographies_response(self):
        rows = [
            {
                "geography_id": "g",
                "country_code": "PK",
                "country_name": "Pakistan",
                "geographic_resolution": "country",
                "region_name": None,
                "code_system": "ISO3166-1-alpha2",
                "observation_count": 3,
                "first_period_start": WEEK_START,
                "latest_period_end": WEEK_END,
            }
        ]
        out = svc.build_geographies(rows, NOW)
        assert (
            out.data[0].observation_count == 3 and "National-level" in out.data[0].resolution_note
        )


# ------------------------------------------------------------------ freshness
class TestFreshness:
    @pytest.mark.parametrize(("age", "stale"), [(0, False), (28, False), (29, True), (400, True)])
    def test_staleness_uses_todays_date(self, age, stale):
        f = svc.freshness(TODAY - timedelta(days=age), TODAY)
        assert (f.data_age_days, f.is_stale, f.stale_after_days) == (age, stale, 28)

    def test_threshold_is_the_engines(self):
        from app.analysis.models import AnalysisConfig

        assert svc.STALE_AFTER_DAYS == AnalysisConfig().stale_after_days


# ------------------------------------------------------------------ observations
class TestObservations:
    def test_missing_values_stay_null_and_flags_parse(self):
        out = svc.build_observations(
            [obs_row(value=None, numerator=None, data_status="missing", total_count=1)],
            limit=10,
            offset=0,
            now=NOW,
        )
        item = out.data[0]
        assert item.value is None and item.data_status == "missing"
        assert out.page.total == 1 and out.page.limit == 10

    def test_source_attribution_and_unverified_terms_are_exposed(self):
        item = svc.build_observations([obs_row(total_count=1)], limit=1, offset=0, now=NOW).data[0]
        assert item.source.slug == "who_flunet" and item.source.terms_verified is False
        assert item.source_url == "https://example.test/flunet"
        assert item.series.surveillance_stream == "sentinel"

    def test_empty_result(self):
        out = svc.build_observations([], limit=10, offset=20, now=NOW)
        assert out.data == [] and (out.page.total, out.page.offset) == (0, 20)

    def test_every_response_carries_the_standing_notices(self):
        notices = " ".join(svc.build_observations([], limit=1, offset=0, now=NOW).meta.notices)
        assert "does not confirm an outbreak" in notices and "never zero" in notices


# ------------------------------------------------------------------ latest
class TestLatest:
    def test_combines_latest_value_freshness_and_signal(self):
        out = svc.build_latest(
            [obs_row()],
            [analysis_row(status="possible_anomaly"), analysis_row("trend", "normal")],
            today=TODAY,
            now=NOW,
        )
        item = out.data[0]
        assert item.signal == "possible_anomaly"
        assert sorted(item.analyses) == ["anomaly_detection", "trend"]
        assert item.freshness.data_age_days == (TODAY - WEEK_END).days

    def test_unanalysed_series_say_so(self):
        assert (
            svc.build_latest([obs_row()], [], today=TODAY, now=NOW).data[0].signal == "not_analysed"
        )

    def test_a_signal_for_an_older_week_is_not_presented_as_current(self):
        old = analysis_row(status="possible_anomaly", period_end=WEEK_END - timedelta(days=7))
        item = svc.build_latest([obs_row()], [old], today=TODAY, now=NOW).data[0]
        assert item.signal == "analysis_outdated"

    def test_analyses_are_matched_per_stream(self):
        out = svc.build_latest(
            [obs_row(surveillance_stream="nonsentinel")],
            [analysis_row(status="possible_anomaly")],  # sentinel analysis
            today=TODAY,
            now=NOW,
        )
        assert out.data[0].signal == "not_analysed"


# ------------------------------------------------------------------ trends
class TestTrends:
    def test_points_are_chronological_and_blank_weeks_stay_null(self):
        rows = [
            obs_row(period_start=WEEK_START, period_end=WEEK_END),
            obs_row(
                period_start=WEEK_START - timedelta(days=14),
                period_end=WEEK_END - timedelta(days=14),
                value=None,
                data_status="missing",
            ),
        ]
        t = svc.build_trends(rows, [analysis_row("trend")], today=TODAY, now=NOW).data[0]
        assert [p.value is None for p in t.points] == [True, False]
        assert t.trend is not None and t.rolling_average is None
        assert t.freshness.latest_period_end == WEEK_END

    def test_separate_series_stay_separate(self):
        rows = [obs_row(), obs_row(surveillance_stream="nonsentinel")]
        assert len(svc.build_trends(rows, [], today=TODAY, now=NOW).data) == 2


# ------------------------------------------------------------------ alerts
class TestAlerts:
    def alerts(self, rows, today=TODAY):
        return svc.build_alerts(rows, today=today, now=NOW)

    def test_only_flagged_anomaly_screenings_are_alerts(self):
        out = self.alerts(
            [
                analysis_row(status="possible_anomaly"),
                analysis_row(status="normal", geography_id="g2"),
                analysis_row("trend", "elevated", geography_id="g3"),  # not an anomaly screening
                analysis_row(status="insufficient_data", geography_id="g4"),
                analysis_row(status="stale_data", geography_id="g5"),
            ]
        )
        assert [a.status for a in out.data] == ["possible_anomaly"]

    def test_every_alert_carries_the_not_an_outbreak_disclaimer(self):
        a = self.alerts([analysis_row(status="elevated")]).data[0]
        assert "does not confirm an outbreak" in a.disclaimer

    def test_alerts_on_now_stale_data_are_withheld_and_counted(self):
        out = self.alerts(
            [analysis_row(status="possible_anomaly")], today=WEEK_END + timedelta(days=60)
        )
        assert out.data == []
        assert any("1 flagged series withheld" in n for n in out.meta.notices)

    def test_strongest_signals_come_first(self):
        out = self.alerts(
            [
                analysis_row(status="elevated", geography_id="g1", score=Decimal("2.5")),
                analysis_row(status="possible_anomaly", geography_id="g2", score=Decimal("4")),
                analysis_row(status="possible_anomaly", geography_id="g3", score=Decimal("9")),
            ]
        )
        assert [a.series.geography_id for a in out.data] == ["g3", "g2", "g1"]

    def test_no_alert_text_claims_an_outbreak(self):
        a = self.alerts([analysis_row(status="possible_anomaly")]).data[0]
        text = (a.disclaimer + " " + " ".join(self.alerts([]).meta.notices)).lower()
        assert "outbreak is" not in text and "outbreak detected" not in text


# ------------------------------------------------------------------ comparisons
def cmp_rows():
    return [
        obs_row(),
        obs_row(
            country_code="US",
            country_name="United States",
            geography_id="g-us",
            value=Decimal("8.0"),
            source_slug="cdc",
            source_name="CDC",
            source_publisher="CDC",
        ),
        obs_row(
            country_code="US",
            country_name="United States",
            geography_id="g-us",
            period_start=WEEK_START - timedelta(days=7),
            period_end=WEEK_END - timedelta(days=7),
            value=Decimal("7.0"),
        ),
    ]


def compare(rows, **kw):
    args = {
        "indicator": "influenza_positivity",
        "stream": "sentinel",
        "countries": ["PK", "US"],
        "now": NOW,
    }
    return svc.build_comparison(rows, **(args | kw))


class TestComparison:
    def test_values_line_up_by_week_and_gaps_are_null_not_zero(self):
        out = compare(cmp_rows())
        assert [w.period_start for w in out.weeks] == [WEEK_START - timedelta(days=7), WEEK_START]
        first, second = out.weeks
        assert first.values["PK"] is None  # Pakistan reported nothing that week
        assert first.values["US"].value == Decimal("7.0")
        assert second.values["PK"].value == Decimal("12.5") and second.values[
            "US"
        ].value == Decimal("8.0")

    def test_numerators_and_denominators_travel_with_every_value(self):
        cell = compare(cmp_rows()).weeks[-1].values["PK"]
        assert (cell.numerator, cell.denominator) == (Decimal("25"), Decimal("200"))

    def test_no_cross_country_difference_or_ranking_is_ever_produced(self):
        dumped = compare(cmp_rows()).model_dump_json().lower()
        for forbidden in ("difference", "rank", "ratio", "winner", "higher_than"):
            assert forbidden not in dumped.replace("no difference, ranking or score", "")

    def test_caveats_always_present_and_extended_when_sources_differ(self):
        out = compare(cmp_rows())
        assert len(out.caveats) >= len(svc.COMPARISON_CAVEATS)
        assert any("different sources" in c for c in out.caveats)

    def test_a_country_without_data_is_listed_not_invented(self):
        out = compare(cmp_rows(), countries=["PK", "US", "IN"])
        assert out.countries_without_data == ["IN"]
        assert {c.country_code for c in out.countries} == {"PK", "US"}

    def test_mixed_geographic_resolution_is_called_out(self):
        rows = cmp_rows()
        rows[1] = rows[1] | {"geographic_resolution": "admin1"}
        assert any("different geographic resolutions" in c for c in compare(rows).caveats)

    def test_small_samples_are_called_out(self):
        rows = cmp_rows()
        rows[0] = rows[0] | {"quality_flags": '["small_denominator"]'}
        assert any("small_denominator" in c for c in compare(rows).caveats)

    @pytest.mark.parametrize(
        "indicator", ["influenza_positive_count", "specimens_processed", "dengue_cases", "x"]
    )
    def test_counts_and_unknown_indicators_are_refused(self, indicator):
        with pytest.raises(svc.ComparisonError) as e:
            compare([], indicator=indicator)
        assert e.value.code == "indicator_not_comparable"
        assert "population" in str(e.value) and "denominator" in str(e.value)

    def test_one_country_is_not_a_comparison(self):
        with pytest.raises(svc.ComparisonError) as e:
            compare(cmp_rows(), countries=["PK", "PK"])
        assert e.value.code == "too_few_countries"

    def test_rows_of_a_non_comparable_type_are_refused(self):
        rows = cmp_rows()
        rows[0] = rows[0] | {"indicator_type": "laboratory_count"}
        with pytest.raises(svc.ComparisonError, match="not comparable"):
            compare(rows)

    def test_different_units_are_refused(self):
        rows = cmp_rows()
        rows[1] = rows[1] | {"unit": "per_100k"}
        with pytest.raises(svc.ComparisonError) as e:
            compare(rows)
        assert e.value.code == "unit_mismatch"

    def test_no_data_at_all_is_a_valid_empty_comparison(self):
        out = compare([])
        assert out.weeks == [] and out.countries_without_data == ["PK", "US"]


# ------------------------------------------------------------------ sources
class TestSources:
    def test_sources_pass_through_with_terms_status(self):
        row = {
            "source_id": "s",
            "slug": "who_flunet",
            "name": "WHO FluNet",
            "publisher": "WHO",
            "source_url": "https://example.test",
            "source_type": "file_download",
            "update_cadence": None,
            "enabled": False,
            "license_name": None,
            "license_url": None,
            "terms_verified": False,
        }
        out = svc.build_sources([row], NOW)
        assert out.data[0].terms_verified is False and out.data[0].enabled is False

    def test_status_and_runs_shapes(self):
        status = {
            "source_id": "s",
            "slug": "x",
            "name": "X",
            "enabled": True,
            "update_cadence": None,
            "last_successful_run_at": None,
            "last_run_status": None,
            "latest_reporting_period_end": None,
            "latest_published_at": None,
            "latest_retrieved_at": None,
        }
        assert svc.build_source_status([status], NOW).data[0].last_run_status is None
        run = {
            "run_id": "r",
            "started_at": NOW,
            "completed_at": None,
            "status": "running",
            "triggered_by": "manual",
            "dry_run": False,
            "records_downloaded": 0,
            "records_inserted": 0,
            "records_updated": 0,
            "records_rejected": 0,
            "checksum": None,
            "source_published_at": None,
            "error_summary": None,
        }
        assert svc.build_runs([run], NOW).data[0].status == "running"


class TestDriverIndependence:
    """Rows with ISO-string dates (as some drivers return them) behave like `date` objects."""

    def test_latest_is_identical_for_string_and_date_columns(self):
        as_text = {"period_start": "2026-10-12", "period_end": "2026-10-18"}
        obs, ana = obs_row(**as_text), analysis_row(status="elevated", **as_text)
        typed_out = svc.build_latest(
            [obs_row()], [analysis_row(status="elevated")], today=TODAY, now=NOW
        )
        text_out = svc.build_latest([obs], [ana], today=TODAY, now=NOW)
        assert text_out == typed_out

    def test_null_dates_stay_null(self):
        rows = [
            {
                "geography_id": "g",
                "country_code": "PK",
                "country_name": "Pakistan",
                "geographic_resolution": "country",
                "region_name": None,
                "code_system": None,
                "observation_count": 0,
                "first_period_start": None,
                "latest_period_end": None,
            }
        ]
        out = svc.build_geographies(rows, NOW)
        assert out.data[0].first_period_start is None


class TestJsonShape:
    def test_numbers_serialise_as_json_numbers_not_strings(self):
        out = svc.build_observations([obs_row(total_count=1)], limit=1, offset=0, now=NOW)
        data = json.loads(out.model_dump_json())["data"][0]
        assert data["value"] == 12.5 and isinstance(data["value"], float)
        assert data["numerator"] == 25 and data["denominator"] == 200

    def test_null_stays_null_in_json(self):
        row = obs_row(value=None, numerator=None, data_status="missing", total_count=1)
        data = json.loads(
            svc.build_observations([row], limit=1, offset=0, now=NOW).model_dump_json()
        )
        assert data["data"][0]["value"] is None

    def test_analysis_numbers_are_numbers(self):
        a = json.loads(svc.analysis(analysis_row()).model_dump_json())
        assert a["observed_value"] == 30 and a["score"] == 20 and a["threshold"] == 3.5

    def test_the_shape_matches_the_published_schema(self):
        schema = svc.s.ObservationOut.model_json_schema(mode="serialization")
        assert schema["properties"]["value"]["anyOf"][0]["type"] == "number"
