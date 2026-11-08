"""Tests for app.ai.prompts -- pure string builders, no SDK or network needed."""

from __future__ import annotations

import datetime
from uuid import uuid4

from app.ai.models import DocumentEvidence, EvidenceBundle, StructuredObservation, StructuredSignal
from app.ai.prompts import (
    SYSTEM_PROMPT,
    render_document_block,
    render_signal_block,
    render_structured_block,
    render_user_prompt,
)


def _obs(**overrides) -> StructuredObservation:
    base = dict(
        geography_id="g1",
        geography_name="Pakistan",
        geographic_resolution="country",
        country_code="PK",
        disease="influenza",
        indicator="influenza_positivity",
        surveillance_stream="sentinel",
        period_start=datetime.date(2026, 10, 5),
        period_end=datetime.date(2026, 10, 11),
        value=12.5,
        unit="percent",
        data_status="reported",
        quality_flags=(),
        source_slug="who_flunet",
        retrieved_at=None,
    )
    base.update(overrides)
    return StructuredObservation(**base)


def _doc(**overrides) -> DocumentEvidence:
    base = dict(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content="Vector control reduces transmission.",
        similarity=0.9,
        title="Dengue",
        publisher="WHO",
        source_url="https://www.who.int/dengue",
        document_type="fact_sheet",
        authority_level="official",
        country_code=None,
        disease="dengue",
        published_at=None,
        retrieved_at=None,
        language="en",
        review_status="approved",
    )
    base.update(overrides)
    return DocumentEvidence(**base)


def _signal(**overrides) -> StructuredSignal:
    base = dict(
        geography_id="g1",
        geography_name="Pakistan",
        country_code="PK",
        disease="dengue",
        indicator="suspected_cases",
        signal="elevated",
        period_start=datetime.date(2026, 10, 5),
        period_end=datetime.date(2026, 10, 11),
        source_slug="surveillance_alerts_engine",
    )
    base.update(overrides)
    return StructuredSignal(**base)


def test_system_prompt_states_the_core_rules():
    assert "only the evidence" in SYSTEM_PROMPT
    assert "Never invent" in SYSTEM_PROMPT
    assert "medical diagnosis" in SYSTEM_PROMPT
    assert "untrusted data" in SYSTEM_PROMPT


def test_system_prompt_requires_grounded_protective_suggestions_and_warns_of_dangerous_myths():
    assert "[T#]" in SYSTEM_PROMPT
    assert "aspirin" in SYSTEM_PROMPT
    assert "never invented" in SYSTEM_PROMPT or "never state a home remedy" in SYSTEM_PROMPT


def test_render_signal_block_numbers_signals_and_names_the_disease():
    bundle = EvidenceBundle(signals=(_signal(),))
    block = render_signal_block(bundle)
    assert "[T1]" in block
    assert "dengue" in block
    assert "elevated" in block
    assert "statistical screening flag" in block


def test_render_signal_block_empty_bundle_says_so():
    assert render_signal_block(EvidenceBundle()) == "(no currently flagged signals)"


def test_render_user_prompt_includes_signal_block():
    bundle = EvidenceBundle(signals=(_signal(),), documents=(_doc(),))
    prompt = render_user_prompt("How do I protect myself?", bundle)
    assert "[T1]" in prompt


def test_render_structured_block_numbers_observations_from_one():
    bundle = EvidenceBundle(structured=(_obs(), _obs(indicator="dengue_cases", unit="count")))
    block = render_structured_block(bundle)
    assert "[S1]" in block
    assert "[S2]" in block
    assert "Pakistan" in block
    assert "12.5 percent" in block


def test_render_structured_block_shows_missing_value_explicitly():
    bundle = EvidenceBundle(structured=(_obs(value=None, data_status="missing", unit=None),))
    block = render_structured_block(bundle)
    assert "no data (missing)" in block


def test_render_structured_block_includes_quality_flags_when_present():
    bundle = EvidenceBundle(structured=(_obs(quality_flags=("small_denominator",)),))
    block = render_structured_block(bundle)
    assert "small_denominator" in block


def test_render_structured_block_empty_bundle_says_so():
    assert render_structured_block(EvidenceBundle()) == "(no structured observations retrieved)"


def test_render_document_block_numbers_documents_from_one_and_shows_authority():
    bundle = EvidenceBundle(
        documents=(_doc(), _doc(title="Measles", authority_level="peer_reviewed"))
    )
    block = render_document_block(bundle)
    assert "[D1]" in block
    assert "[D2]" in block
    assert "official" in block
    assert "peer_reviewed" in block


def test_render_document_block_shows_date_unknown_when_published_at_missing():
    bundle = EvidenceBundle(documents=(_doc(published_at=None),))
    assert "date unknown" in render_document_block(bundle)


def test_render_document_block_empty_bundle_says_so():
    assert render_document_block(EvidenceBundle()) == "(no documents retrieved)"


def test_render_user_prompt_includes_question_and_both_blocks():
    bundle = EvidenceBundle(structured=(_obs(),), documents=(_doc(),))
    prompt = render_user_prompt("How many dengue cases?", bundle)
    assert "How many dengue cases?" in prompt
    assert "[S1]" in prompt
    assert "[D1]" in prompt
    assert "citing [S#]/[D#]/[T#] markers" in prompt
