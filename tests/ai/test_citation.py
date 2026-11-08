"""Tests for app.ai.citation -- step G, "do not create fake citations" (Prompt.txt §11 rule 9)."""

from __future__ import annotations

from app.ai.citation import available_markers, check_citations, extract_markers
from app.ai.models import EvidenceBundle, StructuredObservation, StructuredSignal


def _obs() -> StructuredObservation:
    import datetime

    return StructuredObservation(
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


def _doc():
    import datetime
    from uuid import uuid4

    from app.ai.models import DocumentEvidence

    return DocumentEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content="Dengue prevention depends on vector control.",
        similarity=0.9,
        title="Dengue",
        publisher="WHO",
        source_url="https://www.who.int/dengue",
        document_type="fact_sheet",
        authority_level="official",
        country_code=None,
        disease="dengue",
        published_at=None,
        retrieved_at=datetime.datetime(2026, 11, 1, tzinfo=datetime.UTC),
        language="en",
        review_status="approved",
    )


def _signal() -> StructuredSignal:
    import datetime

    return StructuredSignal(
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


def test_extract_markers_finds_structured_and_document_markers():
    text = "Cases rose [S1] and the WHO recommends prevention [D1][D2]."
    assert extract_markers(text) == {"[S1]", "[D1]", "[D2]"}


def test_extract_markers_finds_signal_markers():
    assert extract_markers("Dengue is currently flagged as elevated [T1].") == {"[T1]"}


def test_available_markers_includes_signals():
    bundle = EvidenceBundle(signals=(_signal(),))
    assert available_markers(bundle) == {"[T1]"}


def test_citation_to_an_unknown_signal_marker_is_unknown():
    bundle = EvidenceBundle(structured=(_obs(),))
    result = check_citations("Dengue is elevated [T1].", bundle)
    assert "[T1]" in result.unknown_markers


def test_extract_markers_empty_when_none_present():
    assert extract_markers("No markers in this sentence.") == set()


def test_available_markers_sized_to_bundle():
    bundle = EvidenceBundle(structured=(_obs(), _obs()), documents=(_doc(),))
    assert available_markers(bundle) == {"[S1]", "[S2]", "[D1]"}


def test_fully_cited_factual_answer_passes():
    bundle = EvidenceBundle(structured=(_obs(),), documents=(_doc(),))
    result = check_citations(
        "Positivity was 12.5% [S1]. Prevention relies on vector control [D1].", bundle
    )
    assert result.all_supported
    assert result.unsupported_markers == ()
    assert result.unknown_markers == ()


def test_citation_to_a_marker_outside_the_bundle_is_unknown():
    bundle = EvidenceBundle(structured=(_obs(),))
    result = check_citations("Positivity was 12.5% [S1], compare to [S2].", bundle)
    assert not result.all_supported
    assert result.unknown_markers == ("[S2]",)


def test_citation_to_a_document_marker_when_only_structured_was_given_is_unknown():
    bundle = EvidenceBundle(structured=(_obs(),))
    result = check_citations("See guidance [D1].", bundle)
    assert "[D1]" in result.unknown_markers


def test_factual_answer_with_no_markers_at_all_is_unsupported():
    bundle = EvidenceBundle(structured=(_obs(),))
    result = check_citations("Positivity was 12.5% this week.", bundle)
    assert not result.all_supported
    assert result.unsupported_markers == ("(no citation markers present)",)


def test_insufficient_evidence_phrasing_is_not_flagged_as_an_uncited_claim():
    bundle = EvidenceBundle()
    result = check_citations(
        "I don't have enough retrieved evidence to answer this reliably.", bundle
    )
    assert result.all_supported


def test_non_factual_prose_with_no_digits_does_not_require_a_marker():
    bundle = EvidenceBundle()
    result = check_citations("Dengue is a mosquito-borne disease.", bundle)
    assert result.all_supported


def test_unknown_and_unsupported_can_combine():
    bundle = EvidenceBundle()  # no evidence at all
    result = check_citations("Cases rose to 500 [S1].", bundle)
    assert not result.all_supported
    assert result.unknown_markers == ("[S1]",)
