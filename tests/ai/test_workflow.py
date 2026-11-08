"""Tests for app.ai.workflow -- the A-H workflow (Prompt.txt §12), run against fakes only.

No real Gemini call, no real database, no real pgvector retrieval has ever been made from this
module in this sandbox; that is the explicit, documented boundary (app/ai/gemini.py, app/rag/sql.py
docstrings). These tests check the workflow's own decision logic: intent classification, evidence
routing, the insufficient-evidence fallback, and the bounded citation-retry loop.
"""

from __future__ import annotations

import datetime

import pytest

from app.ai.models import DocumentEvidence, EvidenceBundle, StructuredObservation, StructuredSignal
from app.ai.workflow import (
    DEFAULT_MATCH_COUNT,
    INSUFFICIENT_EVIDENCE_TEXT,
    answer_question,
    build_citations,
    build_evidence_bundle,
    build_limitations,
    classify_intent,
    gather_top_signal,
    last_update_timestamps,
    reporting_periods,
)
from tests.ai.fakes import (
    FakeDocumentRetriever,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeStructuredDataSource,
    make_question,
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
        retrieved_at=datetime.datetime(2026, 10, 12, tzinfo=datetime.UTC),
    )
    base.update(overrides)
    return StructuredObservation(**base)


def _doc(**overrides) -> DocumentEvidence:
    from uuid import uuid4

    base = dict(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content="Vector control reduces dengue transmission.",
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


# --- A/B: classify_intent -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["How many dengue cases this week?", "Show the current influenza positivity rate."],
)
def test_classify_intent_numerical_questions(text):
    assert classify_intent(text) == "numerical"


def test_classify_intent_phrases_starting_with_what_is_also_pull_in_guidance():
    # "What is" is itself a guidance hint (explanatory framing), so a numerically-flavoured
    # question phrased that way legitimately classifies as "both" rather than "numerical" --
    # this is the safe default: it would be wrong to skip retrieving explanatory evidence.
    assert classify_intent("What is the current influenza positivity rate?") == "both"


@pytest.mark.parametrize(
    "text", ["What are the symptoms of dengue?", "How should I prevent malaria?"]
)
def test_classify_intent_guidance_questions(text):
    assert classify_intent(text) == "guidance"


def test_classify_intent_both_when_question_wants_numbers_and_explanation():
    assert classify_intent("What is the current trend and how is dengue transmitted?") == "both"


def test_classify_intent_defaults_to_guidance_for_ambiguous_text():
    assert classify_intent("Tell me about the situation.") == "guidance"


# --- full workflow: no evidence ------------------------------------------------------------------


async def test_no_evidence_at_all_returns_insufficient_evidence_answer():
    answer = await answer_question(
        make_question("How many dengue cases in Pakistan this week?"),
        structured_source=FakeStructuredDataSource(observations=[]),
        embedder=FakeEmbeddingProvider(),
        retriever=FakeDocumentRetriever(documents=[]),
        llm=FakeLLMProvider(),
    )
    assert answer.insufficient_evidence
    assert answer.answer_text == INSUFFICIENT_EVIDENCE_TEXT
    assert answer.citations == ()


async def test_llm_none_with_evidence_present_still_returns_insufficient_evidence():
    # `llm=None` is a valid, fully-testable configuration (Gemini not configured yet) -- the
    # workflow must never fabricate an answer when it has evidence but no model to draft with.
    answer = await answer_question(
        make_question("How many dengue cases in Pakistan this week?"),
        structured_source=FakeStructuredDataSource(observations=[_obs()]),
        embedder=FakeEmbeddingProvider(),
        retriever=FakeDocumentRetriever(documents=[]),
        llm=None,
    )
    assert answer.insufficient_evidence
    assert answer.model_identifier is None


# --- full workflow: numerical intent only retrieves structured data -----------------------------


async def test_numerical_question_only_fetches_structured_evidence():
    structured = FakeStructuredDataSource(observations=[_obs()])
    retriever = FakeDocumentRetriever(documents=[_doc()])
    answer = await answer_question(
        make_question("How many dengue cases in Pakistan this week?"),
        structured_source=structured,
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
        llm=FakeLLMProvider(answers=["Positivity was 12.5% [S1]."]),
    )
    assert structured.last_call is not None
    assert retriever.last_call is None  # guidance retrieval never ran
    assert not answer.insufficient_evidence
    assert answer.citations[0].marker == "[S1]"


async def test_guidance_question_only_fetches_document_evidence():
    structured = FakeStructuredDataSource(observations=[_obs()])
    retriever = FakeDocumentRetriever(documents=[_doc()])
    answer = await answer_question(
        make_question("What are the symptoms of dengue?"),
        structured_source=structured,
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
        llm=FakeLLMProvider(answers=["Symptoms include fever [D1]."]),
    )
    assert structured.last_call is None  # numerical retrieval never ran
    assert retriever.last_call is not None
    assert answer.citations[0].marker == "[D1]"


async def test_both_intent_fetches_both_kinds_of_evidence():
    structured = FakeStructuredDataSource(observations=[_obs()])
    retriever = FakeDocumentRetriever(documents=[_doc()])
    answer = await answer_question(
        make_question("What is the current trend and how is dengue transmitted?"),
        structured_source=structured,
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
        llm=FakeLLMProvider(answers=["Rate is 12.5% [S1]; spread is vector-borne [D1]."]),
    )
    assert structured.last_call is not None
    assert retriever.last_call is not None
    assert {c.marker for c in answer.citations} == {"[S1]", "[D1]"}


async def test_document_retrieval_passes_match_count_default():
    retriever = FakeDocumentRetriever(documents=[_doc()])
    await answer_question(
        make_question("What are the symptoms of dengue?"),
        structured_source=FakeStructuredDataSource(observations=[]),
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
        llm=FakeLLMProvider(answers=["Fever is common [D1]."]),
    )
    assert retriever.last_call["match_count"] == DEFAULT_MATCH_COUNT


async def test_country_and_disease_scope_is_forwarded_to_both_sources():
    structured = FakeStructuredDataSource(observations=[_obs()])
    retriever = FakeDocumentRetriever(documents=[_doc()])
    await answer_question(
        make_question(
            "What is the trend and how does it spread?", country_code="PK", disease="dengue"
        ),
        structured_source=structured,
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
        llm=FakeLLMProvider(answers=["[S1] and [D1]."]),
    )
    assert structured.last_call["country_code"] == "PK"
    assert structured.last_call["disease"] == "dengue"
    assert retriever.last_call["country_code"] == "PK"
    assert retriever.last_call["disease"] == "dengue"


# --- citation verification / bounded retry --------------------------------------------------


async def test_a_draft_with_a_fabricated_citation_triggers_one_retry_then_succeeds():
    llm = FakeLLMProvider(answers=["Rate is 12.5% [S9] fabricated.", "Rate is 12.5% [S1]."])
    answer = await answer_question(
        make_question("What is the influenza rate?"),
        structured_source=FakeStructuredDataSource(observations=[_obs()]),
        embedder=FakeEmbeddingProvider(),
        retriever=FakeDocumentRetriever(documents=[]),
        llm=llm,
    )
    assert len(llm.calls) == 2
    assert not answer.insufficient_evidence
    assert answer.answer_text == "Rate is 12.5% [S1]."


async def test_retries_are_bounded_and_eventually_fall_back_to_insufficient_evidence():
    llm = FakeLLMProvider(answers=["Always fabricated [S99]."])  # never cites correctly
    answer = await answer_question(
        make_question("What is the influenza rate?"),
        structured_source=FakeStructuredDataSource(observations=[_obs()]),
        embedder=FakeEmbeddingProvider(),
        retriever=FakeDocumentRetriever(documents=[]),
        llm=llm,
    )
    assert len(llm.calls) == 2  # MAX_GENERATION_ATTEMPTS
    assert answer.insufficient_evidence
    assert answer.answer_text == INSUFFICIENT_EVIDENCE_TEXT
    assert "could not be verified" in " ".join(answer.limitations)


async def test_provider_failure_is_handled_gracefully_not_raised():
    llm = FakeLLMProvider(raise_on_call=1)
    answer = await answer_question(
        make_question("What is the influenza rate?"),
        structured_source=FakeStructuredDataSource(observations=[_obs()]),
        embedder=FakeEmbeddingProvider(),
        retriever=FakeDocumentRetriever(documents=[]),
        llm=llm,
    )
    assert answer.insufficient_evidence
    assert answer.answer_text == INSUFFICIENT_EVIDENCE_TEXT


# --- helper functions: citations / periods / timestamps / limitations ---------------------------


def test_build_citations_labels_structured_and_document_evidence_distinctly():
    bundle = EvidenceBundle(structured=(_obs(),), documents=(_doc(),))
    citations = build_citations(bundle)
    assert citations[0].kind == "structured"
    assert citations[0].source_url is None
    assert citations[1].kind == "document"
    assert citations[1].source_url == "https://www.who.int/dengue"


def test_reporting_periods_deduplicates_and_sorts():
    bundle = EvidenceBundle(structured=(_obs(), _obs()))
    assert reporting_periods(bundle) == ("2026-10-05..2026-10-11",)


def test_last_update_timestamps_collects_both_kinds_of_evidence():
    bundle = EvidenceBundle(structured=(_obs(),), documents=(_doc(),))
    stamps = last_update_timestamps(bundle)
    assert len(stamps) == 2


def test_build_limitations_flags_provisional_and_flagged_observations():
    bundle = EvidenceBundle(
        structured=(_obs(data_status="provisional", quality_flags=("input_missing",)),)
    )
    limitations = build_limitations(bundle, "numerical")
    assert any("provisional" in note for note in limitations)
    assert any("quality" in note for note in limitations)


def test_build_limitations_flags_research_grade_documents():
    bundle = EvidenceBundle(documents=(_doc(authority_level="peer_reviewed"),))
    limitations = build_limitations(bundle, "guidance")
    assert any("research finding" in note for note in limitations)


def test_build_limitations_empty_when_everything_is_clean():
    bundle = EvidenceBundle(structured=(_obs(),), documents=(_doc(),))
    assert build_limitations(bundle, "both") == ()


# --- "currently most prevalent" signal feature -----------------------------------------------
# Covers the user-requested enhancement: when a question asks something like "how do I protect
# myself" without naming a disease, the workflow should identify the currently flagged disease
# from real screening data (never guess it) and re-scope document retrieval to it, so the model
# can give specific, cited protective guidance instead of generic advice.


async def test_gather_top_signal_runs_for_current_threat_phrasing_with_no_named_disease():
    source = FakeStructuredDataSource(signals=[_signal()])
    signals = await gather_top_signal(
        make_question("What's going around right now and how do I protect myself?"), source
    )
    assert signals == (_signal(),)
    assert source.last_signal_call is not None


async def test_gather_top_signal_does_not_run_when_disease_is_already_named():
    source = FakeStructuredDataSource(signals=[_signal()])
    signals = await gather_top_signal(
        make_question("How do I protect myself from dengue?", disease="dengue"), source
    )
    assert signals == ()
    assert source.last_signal_call is None  # never overrides an explicitly-named disease


async def test_gather_top_signal_does_not_run_for_ordinary_guidance_questions():
    source = FakeStructuredDataSource(signals=[_signal()])
    signals = await gather_top_signal(make_question("What are the symptoms of dengue?"), source)
    assert signals == ()
    assert source.last_signal_call is None


async def test_build_evidence_bundle_rescopes_document_retrieval_to_the_flagged_disease():
    structured = FakeStructuredDataSource(signals=[_signal(disease="measles")])
    retriever = FakeDocumentRetriever(documents=[_doc(disease="measles")])
    bundle = await build_evidence_bundle(
        make_question("What's spreading right now and how do I protect myself?"),
        "guidance",
        structured_source=structured,
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
    )
    assert bundle.signals == (_signal(disease="measles"),)
    assert retriever.last_call["disease"] == "measles"  # re-scoped, not left as None


async def test_build_evidence_bundle_never_overrides_an_explicit_disease_with_a_signal():
    structured = FakeStructuredDataSource(signals=[_signal(disease="measles")])
    retriever = FakeDocumentRetriever(documents=[_doc()])
    bundle = await build_evidence_bundle(
        make_question("How do I protect myself?", disease="dengue"),
        "guidance",
        structured_source=structured,
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
    )
    assert bundle.signals == ()  # gather_top_signal never ran: disease was already named
    assert retriever.last_call["disease"] == "dengue"


async def test_answer_question_cites_signal_with_t_marker_and_adds_limitation_note():
    structured = FakeStructuredDataSource(signals=[_signal(disease="dengue")])
    retriever = FakeDocumentRetriever(documents=[_doc(disease="dengue")])
    answer = await answer_question(
        make_question("What's going around right now? How do I protect myself?"),
        structured_source=structured,
        embedder=FakeEmbeddingProvider(),
        retriever=retriever,
        llm=FakeLLMProvider(
            answers=["Dengue is currently flagged as elevated [T1]. Use mosquito nets [D1]."]
        ),
    )
    assert not answer.insufficient_evidence
    markers = {c.marker for c in answer.citations}
    assert "[T1]" in markers
    assert "[D1]" in markers
    assert any("selected automatically" in note for note in answer.limitations)


def test_build_citations_includes_signal_with_t_marker_and_label():
    bundle = EvidenceBundle(signals=(_signal(),))
    citations = build_citations(bundle)
    assert citations[0].marker == "[T1]"
    assert citations[0].kind == "signal"
    assert "dengue" in citations[0].label
    assert "elevated" in citations[0].label
