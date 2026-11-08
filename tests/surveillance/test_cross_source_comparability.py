"""Phase 7: prove the new sources (CDC FluView, NIH Pakistan bulletins) cannot be silently
combined with each other or with WHO FluNet by the comparison endpoint, and that their geographic
scope cannot leak into the wrong country.

This is a guard-rail test, not a parser test (those live in tests/ingestion/). It answers one
question: given real observations produced by each connector, does
app.surveillance.service.validate_comparison_request / build_comparison actually stop the mixes
the master spec prohibits ("do not combine incompatible indicators", "never treat research
findings as current case counts")? Each test below constructs the request the comparison
endpoint would see and asserts it is refused, or asserts the geography/indicator-type boundary
that keeps it from ever reaching that endpoint with the wrong label.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.surveillance.service as svc
from app.datasets.nih_table import parse_province_tables
from app.ingestion.connectors.cdc_fluview_parser import (
    parse_ilinet_csv,
    parse_nrevss_clinical_csv,
    parse_nrevss_public_health_csv,
)
from app.rag.extract import extract_pdf

REPO_ROOT = Path(__file__).resolve().parents[2]
CDC_DIR = REPO_ROOT / "data" / "cdc_fluview"
NIH_PDF = (
    REPO_ROOT / "data" / "nih_bulletins" / "Public_Health_Bulletin_IDSRS_Weekly_Report-26-2026.pdf"
)
US = frozenset({"US"})


def _cdc_ilinet_observations():
    content = (CDC_DIR / "ILINet.csv").read_bytes()
    return parse_ilinet_csv(content, source_url="https://example.test", countries=US).observations


def _cdc_nrevss_clinical_observations():
    content = (CDC_DIR / "ICL_NREVSS_Clinical_Labs.csv").read_bytes()
    return parse_nrevss_clinical_csv(
        content, source_url="https://example.test", countries=US
    ).observations


def _cdc_nrevss_public_health_observations():
    content = (CDC_DIR / "ICL_NREVSS_Public_Health_Labs.csv").read_bytes()
    return parse_nrevss_public_health_csv(
        content, source_url="https://example.test", countries=US
    ).observations


def _nih_observations():
    """Real NIH bulletin text, parsed to totals -- enough to assert geography/indicator shape
    without re-implementing tests/ingestion/test_nih_bulletin_connector.py's full connector
    round-trip."""
    doc = extract_pdf(NIH_PDF.read_bytes())
    text = "\n".join(page.text for page in doc.pages)
    return parse_province_tables(text)


# --------------------------------------------------------------------------- indicator isolation


class TestNewCdcIndicatorsAreNotAutoComparable:
    """Every new indicator this phase introduces must be absent from the cross-country
    comparison allowlist until a human has verified it belongs there -- the allowlist is an
    opt-in, not a default."""

    @pytest.mark.parametrize(
        "indicator",
        ["ili_pct_weighted", "influenza_positivity_clinical", "influenza_positivity_public_health"],
    )
    def test_comparison_endpoint_refuses_the_new_indicator_slugs(self, indicator):
        with pytest.raises(svc.ComparisonError, match="cannot be compared across countries"):
            svc.validate_comparison_request(indicator, ["US", "PK"])

    def test_the_new_slugs_are_distinct_from_each_other_and_from_flunet(self):
        observed = {
            o.indicator
            for o in (
                _cdc_ilinet_observations()
                + _cdc_nrevss_clinical_observations()
                + _cdc_nrevss_public_health_observations()
            )
        }
        assert observed == {
            "ili_pct_weighted",
            "influenza_positivity_clinical",
            "influenza_positivity_public_health",
        }
        # WHO FluNet's lab-positivity slug, already in the comparison allowlist -- these three
        # must never collide with it, or CDC rows would start answering FluNet comparisons.
        assert "influenza_positivity" not in observed


class TestNihIndicatorIsExcludedFromRateComparisons:
    """NIH's suspected-case counts have no denominator and no lab confirmation; comparing them
    as if they were a rate (population- or testing-adjusted) would be exactly the kind of
    incompatible-indicator mixing the data-expansion request prohibited."""

    def test_nih_indicator_type_is_not_in_the_comparable_set(self):
        parsed = _nih_observations()
        assert parsed.totals, "fixture bulletin produced no totals -- test fixture is stale"
        # NihBulletinConnector always emits indicator_type="other" for this table (see its module
        # docstring); re-derive that here from the one place it's defined, so this test breaks
        # if that invariant ever changes instead of silently drifting out of date.
        from app.ingestion.connectors.nih_bulletin import _to_observation

        total = parsed.totals[0]
        by_province = [
            c
            for c in parsed.counts
            if (c.year, c.week, c.disease)
            == (
                total.year,
                total.week,
                total.disease,
            )
        ]
        obs = _to_observation(total, source_url="https://example.test", by_province=by_province)
        assert obs.indicator_type not in svc.COMPARABLE_INDICATOR_TYPES
        assert obs.unit == "count"  # a raw count, never silently treated as a rate


# ---------------------------------------------------------------------------- geographic scope


class TestGeographicScopeCannotLeak:
    """A connector built for one country must never emit a row for another country, however its
    input data is filtered or reused -- this is what stops, e.g., a CDC row being mistaken for a
    Pakistan observation downstream."""

    def test_cdc_fluview_only_ever_emits_united_states_rows(self):
        observations = (
            _cdc_ilinet_observations()
            + _cdc_nrevss_clinical_observations()
            + _cdc_nrevss_public_health_observations()
        )
        assert observations, "fixture CSVs produced no rows -- test fixture is stale"
        assert {o.country_code for o in observations} == {"US"}

    def test_cdc_fluview_requested_for_pakistan_alone_yields_nothing(self):
        """The connector's countries filter is the enforcement point: asking it for PK must
        produce zero US-shaped rows, not a silently-relabelled one."""
        content = (CDC_DIR / "ILINet.csv").read_bytes()
        result = parse_ilinet_csv(
            content, source_url="https://example.test", countries=frozenset({"PK"})
        )
        assert result.observations == []
        assert result.rows_out_of_scope > 0

    def test_nih_bulletin_only_ever_emits_pakistan_rows(self):
        from app.ingestion.connectors.nih_bulletin import _to_observation

        parsed = _nih_observations()
        assert parsed.totals
        obs_list = []
        for total in parsed.totals:
            by_province = [
                c
                for c in parsed.counts
                if (c.year, c.week, c.disease) == (total.year, total.week, total.disease)
            ]
            obs_list.append(
                _to_observation(total, source_url="https://example.test", by_province=by_province)
            )
        assert {o.country_code for o in obs_list} == {"PK"}


# ------------------------------------------------------------------------------------- unit mix


class TestNrevssLabTypesStayDistinctThoughSameShapeUnit:
    """CDC's two NREVSS releases (clinical vs public-health labs) both produce a 'percent'
    laboratory_positivity row, which is exactly the situation where accidental mixing is easiest
    -- same type, same unit, different population of specimens. They must carry different
    indicator slugs and different metadata lab-type tags, never be assumed interchangeable."""

    def test_clinical_and_public_health_rows_carry_different_indicators_and_lab_type_tags(self):
        clinical = _cdc_nrevss_clinical_observations()
        public_health = _cdc_nrevss_public_health_observations()
        assert clinical and public_health
        assert {o.indicator for o in clinical} == {"influenza_positivity_clinical"}
        assert {o.indicator for o in public_health} == {"influenza_positivity_public_health"}
        assert all(o.metadata.get("cdc_lab_type") == "clinical" for o in clinical)
        assert all(o.metadata.get("cdc_lab_type") == "public_health" for o in public_health)
        # Same indicator_type and unit by design (both are lab positivity percentages) -- which
        # is exactly why the distinct indicator slug, not the type or unit, is what has to carry
        # the "do not pool these" boundary.
        assert {o.indicator_type for o in clinical} == {o.indicator_type for o in public_health}
        assert {o.unit for o in clinical} == {o.unit for o in public_health}
