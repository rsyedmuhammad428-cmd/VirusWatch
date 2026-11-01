from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app import cli
from app.ingestion.connectors import available_sources, get_connector
from app.ingestion.connectors.flunet import FLUNET_ENDPOINT, FluNetConnector
from app.ingestion.countries import SUPPORTED_COUNTRIES, resolve_countries
from app.ingestion.errors import IngestionError
from app.ingestion.models import (
    IngestRequest,
    ObservationRecord,
    ParseResult,
    RowRejection,
    RunStatus,
    RunSummary,
)
from app.ingestion.serialization import observation_to_json
from tests.ingestion.fakes import FakeFetcher, ok
from tests.ingestion.flunet_helpers import csv_bytes, row

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def req(**kw) -> IngestRequest:
    return IngestRequest(**{"countries": frozenset({"PAK"}), **kw})


# ------------------------------------------------------------------ connector
class TestFluNetConnector:
    def test_filter_is_sorted_and_deterministic(self):
        f = FluNetConnector.odata_filter(req(countries=frozenset({"USA", "PAK"})))
        assert f == "(COUNTRY_CODE eq 'PAK' or COUNTRY_CODE eq 'USA')"

    def test_filter_with_since_year(self):
        assert FluNetConnector.odata_filter(req(since_year=2015)).endswith(" and ISO_YEAR ge 2015")

    @pytest.mark.parametrize(
        "bad",
        [frozenset(), frozenset({"pak"}), frozenset({"PA"}), frozenset({"PAK' or 1 eq 1 or 'x"})],
    )
    def test_filter_refuses_anything_but_alpha3_codes(self, bad):
        with pytest.raises(ValueError):
            FluNetConnector.odata_filter(req(countries=bad))

    @pytest.mark.parametrize("year", [1800, 2200])
    def test_filter_refuses_absurd_years(self, year):
        with pytest.raises(ValueError):
            FluNetConnector.odata_filter(req(since_year=year))

    def test_source_url_and_query_parameters(self):
        c = FluNetConnector()
        assert c.source_url == f"{FLUNET_ENDPOINT}?$format=csv"
        q = c.query_parameters(req())
        assert q["$format"] == "csv" and q["endpoint"] == FLUNET_ENDPOINT

    def test_signature_is_stable_and_sensitive_to_every_input(self):
        c = FluNetConnector()
        base = c.signature(req(since_year=2020))
        assert base == c.signature(req(since_year=2020))
        assert base != c.signature(req(since_year=2021))
        assert base != c.signature(req(since_year=2020, countries=frozenset({"PAK", "USA"})))
        other = FluNetConnector()
        other.parser_version = "flunet-99"
        assert base != other.signature(req(since_year=2020))

    def test_signature_ignores_flags_that_do_not_change_the_download(self):
        c = FluNetConnector()
        assert c.signature(req()) == c.signature(
            req(dry_run=True, force=True, triggered_by="backfill")
        )

    def test_fetch_builds_a_snapshot_with_provenance(self):
        body = csv_bytes(row())
        c = FluNetConnector(clock=lambda: NOW)
        snapshot = asyncio.run(c.fetch(req(), FakeFetcher(ok(body))))
        assert snapshot.content == body and snapshot.retrieved_at == NOW
        assert snapshot.url == c.source_url and snapshot.checksum.startswith("sha256:")

    def test_parse_delegates_with_the_request_scope(self):
        c = FluNetConnector(clock=lambda: NOW)
        body = csv_bytes(row(), row(COUNTRY_CODE="USA"))
        snapshot = asyncio.run(c.fetch(req(), FakeFetcher(ok(body))))
        parsed = c.parse(snapshot, req())
        assert {o.country_code for o in parsed.observations} == {
            "PK"
        } and parsed.rows_out_of_scope == 1


class TestRegistry:
    def test_only_verified_sources_are_registered(self):
        assert available_sources() == ["who_flunet"]

    def test_get_connector_passes_options_through(self):
        c = get_connector("who_flunet", max_bytes=5, timeout_seconds=7)
        assert isinstance(c, FluNetConnector) and c._max_bytes == 5 and c._timeout == 7

    @pytest.mark.parametrize("slug", ["cdc_fluview", "pk_nih_weekly_bulletin", "nonsense"])
    def test_unimplemented_sources_are_refused_with_guidance(self, slug):
        with pytest.raises(IngestionError, match="who_flunet"):
            get_connector(slug)


# ------------------------------------------------------------------ countries
class TestCountries:
    def test_codes_are_well_formed_and_unique(self):
        iso2 = [c.iso2 for c in SUPPORTED_COUNTRIES.values()]
        assert len(iso2) == len(set(iso2))
        assert all(
            len(k) == 3 and k == c.iso3 and len(c.iso2) == 2 for k, c in SUPPORTED_COUNTRIES.items()
        )

    def test_pakistan_and_us_match_the_seeded_geographies(self):
        assert (SUPPORTED_COUNTRIES["PAK"].iso2, SUPPORTED_COUNTRIES["PAK"].name) == (
            "PK",
            "Pakistan",
        )
        assert (SUPPORTED_COUNTRIES["USA"].iso2, SUPPORTED_COUNTRIES["USA"].name) == (
            "US",
            "United States",
        )

    def test_resolve_normalises_and_dedupes(self):
        assert resolve_countries([" pak", "PAK", "usa "]) == frozenset({"PAK", "USA"})

    def test_resolve_rejects_unknown_with_the_supported_list(self):
        with pytest.raises(IngestionError, match="ZZZ.*Supported: .*PAK"):
            resolve_countries(["PAK", "ZZZ"])

    @pytest.mark.parametrize("empty", [[], [""], ["  "]])
    def test_resolve_requires_at_least_one(self, empty):
        with pytest.raises(IngestionError):
            resolve_countries(empty)


# ------------------------------------------------------------------ models
def obs(**kw) -> ObservationRecord:
    base = dict(
        country_code="PK",
        disease="influenza",
        indicator="influenza_positivity",
        indicator_type="laboratory_positivity",
        reporting_period_start=date(2024, 1, 1),
        reporting_period_end=date(2024, 1, 7),
        value_numeric=Decimal("5"),
        unit="percent",
        source_record_id="r",
        source_url="https://example.test",
    )
    return ObservationRecord(**{**base, **kw})


class TestObservationRecord:
    def test_valid_record(self):
        assert obs().natural_key == (
            "PK",
            "influenza",
            None,
            "influenza_positivity",
            date(2024, 1, 1),
            date(2024, 1, 7),
            None,
        )

    @pytest.mark.parametrize(
        "kw,fragment",
        [
            ({"reporting_period_end": date(2023, 12, 31)}, "precedes"),
            ({"value_numeric": None}, "needs a value"),
            ({"data_status": "missing"}, "must not carry a value"),
            ({"unit": None}, "needs a unit"),
            ({"value_numeric": Decimal("NaN")}, "finite"),
            ({"denominator": Decimal(-1)}, "negative"),
            ({"country_code": "pk"}, "alpha-2"),
            ({"disease": "Influenza A"}, "snake_case"),
            ({"pathogen": "H3N2!"}, "snake_case"),
            ({"indicator_type": "vibes"}, "indicator_type"),
            ({"surveillance_stream": "random"}, "surveillance_stream"),
        ],
    )
    def test_mirrors_the_database_constraints(self, kw, fragment):
        with pytest.raises(ValidationError, match=fragment):
            obs(**kw)

    def test_missing_observation_is_valid_without_a_value(self):
        o = obs(value_numeric=None, data_status="missing")
        assert o.value_numeric is None

    def test_records_are_immutable(self):
        with pytest.raises(ValidationError):
            obs().value_numeric = Decimal(1)

    def test_stream_is_part_of_the_natural_key(self):
        assert (
            obs(surveillance_stream="sentinel").natural_key
            != obs(surveillance_stream="nonsentinel").natural_key
        )


class TestParseResult:
    def test_derived_properties(self):
        r = ParseResult(
            rows_read=10,
            rows_out_of_scope=3,
            observations=[obs(), obs(reporting_period_end=date(2024, 1, 9))],
        )
        r.rejections += [RowRejection(2, "a"), RowRejection(2, "b"), RowRejection(5, "a")]
        assert r.rows_in_scope == 7 and r.rows_rejected == 2  # row 2 counted once
        assert r.latest_period_end == date(2024, 1, 9)
        assert r.reject_reason_counts() == {"a": 2, "b": 1}

    def test_empty_result(self):
        assert ParseResult().latest_period_end is None


# ------------------------------------------------------------------ serialisation
class TestSerialization:
    def test_json_shape_and_full_precision(self):
        o = obs(
            value_numeric=Decimal("26.3158"),
            numerator=Decimal(25),
            denominator=Decimal(95),
            quality_flags=("derived_by_viruswatch",),
            metadata={"source_columns": ["INF_ALL"]},
        )
        j = observation_to_json(o)
        assert (
            j["value_numeric"] == "26.3158" and j["numerator"] == "25" and j["denominator"] == "95"
        )
        assert j["reporting_period_start"] == "2024-01-01" and j["quality_flags"] == [
            "derived_by_viruswatch"
        ]
        assert json.loads(json.dumps(j)) == j  # JSON-safe

    def test_no_exponent_notation_for_whole_numbers(self):
        assert observation_to_json(obs(value_numeric=Decimal("1E+2")))["value_numeric"] == "100"

    def test_missing_values_stay_null(self):
        j = observation_to_json(obs(value_numeric=None, data_status="missing"))
        assert j["value_numeric"] is None and j["data_status"] == "missing"

    def test_keys_match_the_sql_recordset_columns(self):
        from app.ingestion import sql

        declared = sql.UPSERT_OBSERVATIONS.split("as r(")[1].split(")\n),")[0]
        columns = {part.strip().split()[0] for part in declared.replace("\n", " ").split(",")}
        assert columns == set(observation_to_json(obs()))


# ------------------------------------------------------------------ CLI
def parse_args(*argv):
    return cli.build_parser().parse_args(["ingest", "--source", "who_flunet", *argv])


class TestCli:
    TODAY = date(2026, 10, 30)  # ISO year 2026

    def request(self, *argv, default="PAK,USA"):
        return cli.build_request(parse_args(*argv), default_countries=default, today=self.TODAY)

    def test_incremental_default_window_and_countries(self):
        r = self.request()
        assert r.countries == frozenset({"PAK", "USA"}) and r.since_year == 2025
        assert (r.triggered_by, r.dry_run, r.force) == ("manual", False, False)

    def test_backfill_means_all_history(self):
        r = self.request("--backfill")
        assert r.since_year is None and r.triggered_by == "backfill"

    def test_explicit_since_year_wins_over_backfill_default(self):
        assert self.request("--backfill", "--since-year", "2010").since_year == 2010

    def test_countries_option_overrides_settings_and_normalises(self):
        assert self.request("--countries", "pak, usa ").countries == frozenset({"PAK", "USA"})

    def test_unsupported_country_is_an_error(self):
        with pytest.raises(IngestionError, match="Unsupported"):
            self.request("--countries", "PAK,ZZZ")

    def test_flags(self):
        r = self.request("--dry-run", "--force", "--scheduled")
        assert r.dry_run and r.force and r.triggered_by == "scheduled"

    def test_backfill_and_scheduled_conflict(self):
        with pytest.raises(IngestionError, match="cannot be combined"):
            self.request("--backfill", "--scheduled")

    def test_source_is_required(self):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["ingest"])
        assert exc.value.code == 2

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([])

    @pytest.mark.parametrize(
        "status,code",
        [
            (RunStatus.SUCCEEDED, 0),
            (RunStatus.SKIPPED_UNCHANGED, 0),
            (RunStatus.SKIPPED_DISABLED, 0),
            (RunStatus.FAILED, 1),
            (RunStatus.PARTIAL, 2),
            (RunStatus.SKIPPED_ALREADY_RUNNING, 3),
        ],
    )
    def test_exit_codes_cover_every_status(self, status, code):
        assert cli.exit_code(status) == code

    def test_every_status_has_an_exit_code(self):
        assert {s for s in RunStatus} == {s for s in RunStatus if cli.exit_code(s) is not None}

    def test_summary_text(self):
        text = cli.format_summary(
            RunSummary(
                RunStatus.PARTIAL,
                "run-1",
                "who_flunet",
                True,
                rows_read=10,
                rows_rejected=1,
                rows_out_of_scope=2,
                observations_inserted=7,
                observations_updated=1,
                observations_unchanged=3,
                checksum="sha256:ab",
                latest_period_end=date(2024, 1, 7),
                reject_reasons={"invalid_count": 1},
                error=None,
            )
        )
        for fragment in (
            "DRY RUN",
            "partial",
            "run-1",
            "sha256:ab",
            "10 read, 2 out of scope, 1 rejected",
            "7 new, 1 revised, 3 unchanged",
            "2024-01-07",
            "invalid_count",
        ):
            assert fragment in text

    def test_failed_summary_shows_the_error(self):
        text = cli.format_summary(
            RunSummary(RunStatus.FAILED, None, "who_flunet", False, error="HTTP 404 from source")
        )
        assert "HTTP 404 from source" in text and "DRY RUN" not in text

    def test_main_reports_configuration_errors_without_a_traceback(self, capsys, monkeypatch):
        async def boom(_args):
            raise IngestionError("SUPABASE_DB_URL is not set.")

        monkeypatch.setattr(cli, "_ingest", boom)
        assert cli.main(["ingest", "--source", "who_flunet"]) == 1
        assert "SUPABASE_DB_URL is not set." in capsys.readouterr().err
