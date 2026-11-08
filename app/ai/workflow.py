"""The AI workflow (Prompt.txt §12, steps A-H) as ordinary tested service functions.

"Build the workflow as ordinary tested service functions first. Introduce LangGraph only if a
genuine need for persistent multistep orchestration arises" (promprt2.txt). This module *is*
that first build; app.ai.langgraph_adapter wraps these same functions in a StateGraph for
callers that want LangGraph's execution model, without duplicating any decision logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.citation import check_citations
from app.ai.gemini import LLMError
from app.ai.models import (
    Citation,
    EvidenceBundle,
    QuestionIntent,
    QuestionRequest,
    StructuredSignal,
    WorkflowAnswer,
)
from app.ai.ports import (
    DocumentRetriever,
    EmbeddingProvider,
    GenerationRequest,
    LLMProvider,
    StructuredDataSource,
)
from app.ai.prompts import SYSTEM_PROMPT, render_user_prompt

MAX_GENERATION_ATTEMPTS = 2  # bounded retries (promprt2.txt): one first try + one citation retry
DEFAULT_MATCH_COUNT = 6
DEFAULT_MAX_OUTPUT_TOKENS = 1024
DEFAULT_TIMEOUT_SECONDS = 20.0

INSUFFICIENT_EVIDENCE_TEXT = (
    "I don't have enough retrieved evidence to answer this reliably. Please check the "
    "surveillance dashboard directly, or ask a more specific question (disease, country, time "
    "period) so I can look for matching observations and documents."
)

# Keywords that make a question look like it wants a number out of the database rather than
# (or in addition to) general explanation. Deliberately conservative: this only routes retrieval,
# it never decides what the answer says (spec rule: numbers always come from deterministic data).
_NUMERICAL_HINTS = re.compile(
    r"\b(how many|how much|trend|rate|positivity|count|cases?|number of|compare|comparison|"
    r"increase|decrease|rising|falling|latest|current|this week|last week|percent|%)\b",
    re.IGNORECASE,
)
_GUIDANCE_HINTS = re.compile(
    r"\b(what is|what are|explain|symptom|prevent|treatment|transmit|guidance|advice|should i|"
    r"risk factor|how does|why)\b",
    re.IGNORECASE,
)
_CURRENT_THREAT_HINTS = re.compile(
    r"\b(most viral|most common virus|most dangerous|what'?s (spreading|going around|trending)|"
    r"currently spreading|right now|protect (myself|yourself)|how (do|can) i protect|"
    r"how (do|can) i (prevent|avoid) (getting|catching)|keep (myself|my family) safe)\b",
    re.IGNORECASE,
)


# --- A/B: interpret the question --------------------------------------------------------------


def classify_intent(question_text: str) -> QuestionIntent:
    wants_numbers = bool(_NUMERICAL_HINTS.search(question_text))
    wants_guidance = bool(_GUIDANCE_HINTS.search(question_text))
    if wants_numbers and wants_guidance:
        return "both"
    if wants_numbers:
        return "numerical"
    return "guidance"  # the safe default: never silently skip retrieving explanatory evidence


# --- C/D: retrieve evidence --------------------------------------------------------------------


async def gather_structured_evidence(
    request: QuestionRequest, source: StructuredDataSource, *, limit: int = 20
):
    observations = await source.fetch_observations(
        country_code=request.country_code, disease=request.disease, limit=limit
    )
    return tuple(observations)


async def gather_document_evidence(
    request: QuestionRequest,
    embedder: EmbeddingProvider,
    retriever: DocumentRetriever,
    *,
    disease: str | None,
    match_count: int = DEFAULT_MATCH_COUNT,
):
    query_embedding = await embedder.embed_query(request.text)
    documents = await retriever.retrieve(
        query_embedding,
        country_code=request.country_code,
        disease=disease,
        match_count=match_count,
    )
    return tuple(documents)


async def gather_top_signal(
    request: QuestionRequest, source: StructuredDataSource, *, limit: int = 5
) -> tuple[StructuredSignal, ...]:
    """Only runs when the caller did not already name a disease (never overrides an explicit
    scope) and the question reads like "what's currently most prevalent / how do I protect
    myself". Reuses app.surveillance.service.build_alerts()'s exact output via
    StructuredDataSource.fetch_top_signals -- the workflow never decides what's "trending" on
    its own, it only cites what the deterministic analysis engine already flagged.
    """
    if request.disease is not None:
        return ()
    if not _CURRENT_THREAT_HINTS.search(request.text):
        return ()
    signals = await source.fetch_top_signals(country_code=request.country_code, limit=limit)
    return tuple(signals)


async def build_evidence_bundle(
    request: QuestionRequest,
    intent: QuestionIntent,
    *,
    structured_source: StructuredDataSource,
    embedder: EmbeddingProvider,
    retriever: DocumentRetriever,
) -> EvidenceBundle:
    structured = (
        await gather_structured_evidence(request, structured_source)
        if intent in ("numerical", "both")
        else ()
    )
    signals = (
        await gather_top_signal(request, structured_source)
        if intent in ("guidance", "both")
        else ()
    )
    # If the engine has already flagged a disease as currently elevated, re-scope document
    # retrieval to that disease's real prevention guidance instead of a generic, unscoped search
    # -- but never override a disease the caller explicitly named.
    effective_disease = request.disease
    if effective_disease is None and signals:
        effective_disease = signals[0].disease
    documents = (
        await gather_document_evidence(request, embedder, retriever, disease=effective_disease)
        if intent in ("guidance", "both")
        else ()
    )
    return EvidenceBundle(structured=structured, documents=documents, signals=signals)


# --- H helpers: turn the bundle into citations / periods / timestamps --------------------------


def build_citations(bundle: EvidenceBundle) -> tuple[Citation, ...]:
    citations: list[Citation] = []
    for i, obs in enumerate(bundle.structured, start=1):
        citations.append(
            Citation(
                marker=f"[S{i}]",
                kind="structured",
                label=f"{obs.geography_name} {obs.disease}/{obs.indicator} "
                f"{obs.period_start}..{obs.period_end}",
                source_url=None,
                retrieved_at=obs.retrieved_at,
            )
        )
    for i, doc in enumerate(bundle.documents, start=1):
        citations.append(
            Citation(
                marker=f"[D{i}]",
                kind="document",
                label=f"{doc.title} ({doc.publisher})",
                source_url=doc.source_url,
                retrieved_at=doc.retrieved_at,
            )
        )
    for i, sig in enumerate(bundle.signals, start=1):
        citations.append(
            Citation(
                marker=f"[T{i}]",
                kind="signal",
                label=f"{sig.geography_name} {sig.disease}/{sig.indicator} flagged {sig.signal} "
                f"{sig.period_start}..{sig.period_end}",
                source_url=None,
                retrieved_at=None,
            )
        )
    return tuple(citations)


def reporting_periods(bundle: EvidenceBundle) -> tuple[str, ...]:
    periods = {
        f"{o.period_start.isoformat()}..{o.period_end.isoformat()}" for o in bundle.structured
    }
    return tuple(sorted(periods))


def last_update_timestamps(bundle: EvidenceBundle) -> tuple[str, ...]:
    stamps: set[str] = set()
    for o in bundle.structured:
        if o.retrieved_at:
            stamps.add(o.retrieved_at.isoformat())
    for d in bundle.documents:
        if d.retrieved_at:
            stamps.add(d.retrieved_at.isoformat())
    return tuple(sorted(stamps))


def build_limitations(bundle: EvidenceBundle, intent: QuestionIntent) -> tuple[str, ...]:
    notes: list[str] = []
    if intent in ("numerical", "both") and not bundle.structured:
        notes.append("No matching structured observations were found for this question's scope.")
    if intent in ("guidance", "both") and not bundle.documents:
        notes.append("No approved reference documents matched this question.")
    if any(o.data_status != "reported" for o in bundle.structured):
        notes.append("Some observations are provisional, revised or estimated, not final counts.")
    if any(o.quality_flags for o in bundle.structured):
        notes.append("Some observations carry data-quality flags; treat them as approximate.")
    if any(d.authority_level == "peer_reviewed" for d in bundle.documents):
        notes.append("Some cited material is a research finding, not an official recommendation.")
    if bundle.signals:
        notes.append(
            "The disease discussed was selected automatically from currently flagged "
            "surveillance signals [T#], not named by the question; ask about a specific "
            "disease for a more targeted answer."
        )
    return tuple(notes)


# --- F/G: generate and verify -------------------------------------------------------------------


@dataclass(frozen=True)
class _DraftResult:
    text: str
    model_identifier: str | None
    citation_ok: bool


async def _generate_with_bounded_retries(
    request: QuestionRequest, bundle: EvidenceBundle, llm: LLMProvider | None
) -> _DraftResult:
    if llm is None or bundle.is_empty:
        return _DraftResult(text="", model_identifier=None, citation_ok=False)

    user_prompt = render_user_prompt(request.text, bundle)
    last_text = ""
    model_identifier: str | None = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        prompt = user_prompt
        if attempt > 1:
            prompt += (
                "\n\nYour previous answer cited evidence that was not supplied, or made a claim "
                "without a [S#]/[D#] marker. Answer again using ONLY the [S#]/[D#] markers listed "
                "above, or say the evidence is insufficient."
            )
        try:
            result = await llm.generate(
                GenerationRequest(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                    max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                    timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                )
            )
        except LLMError:
            return _DraftResult(text="", model_identifier=None, citation_ok=False)
        last_text = result.text
        model_identifier = result.model_identifier
        check = check_citations(last_text, bundle)
        if check.all_supported:
            return _DraftResult(text=last_text, model_identifier=model_identifier, citation_ok=True)
    return _DraftResult(text=last_text, model_identifier=model_identifier, citation_ok=False)


async def answer_question(
    request: QuestionRequest,
    *,
    structured_source: StructuredDataSource,
    embedder: EmbeddingProvider,
    retriever: DocumentRetriever,
    llm: LLMProvider | None,
) -> WorkflowAnswer:
    """Steps A-H in one call. `llm=None` is a valid, fully-testable configuration (no API key
    configured yet) that always returns the insufficient-evidence answer, never a fabricated one.
    """
    intent = classify_intent(request.text)  # A, B
    bundle = await build_evidence_bundle(  # C, D (E is a no-op: no new arithmetic is ever done)
        request,
        intent,
        structured_source=structured_source,
        embedder=embedder,
        retriever=retriever,
    )
    limitations = build_limitations(bundle, intent)

    if bundle.is_empty:
        return WorkflowAnswer(
            answer_text=INSUFFICIENT_EVIDENCE_TEXT,
            citations=(),
            reporting_periods=(),
            last_update_timestamps=(),
            limitations=limitations or ("No evidence was available for this question.",),
            intent=intent,
            insufficient_evidence=True,
        )

    draft = await _generate_with_bounded_retries(request, bundle, llm)  # F, G
    citations = build_citations(bundle)

    if not draft.citation_ok:
        return WorkflowAnswer(
            answer_text=INSUFFICIENT_EVIDENCE_TEXT,
            citations=(),
            reporting_periods=reporting_periods(bundle),
            last_update_timestamps=last_update_timestamps(bundle),
            limitations=limitations
            + ("The draft answer could not be verified against the retrieved evidence.",),
            intent=intent,
            insufficient_evidence=True,
            model_identifier=draft.model_identifier,
        )

    return WorkflowAnswer(  # H
        answer_text=draft.text,
        citations=citations,
        reporting_periods=reporting_periods(bundle),
        last_update_timestamps=last_update_timestamps(bundle),
        limitations=limitations,
        intent=intent,
        insufficient_evidence=False,
        model_identifier=draft.model_identifier,
    )
