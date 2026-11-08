"""Data shapes for the AI workflow (Prompt.txt §12, steps A-H).

Everything here is a plain, frozen dataclass: the workflow is "ordinary tested service
functions first" (promprt2.txt), not something that needs an LLM's object model to describe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal
from uuid import UUID

WORKFLOW_VERSION = "ai-workflow-1"

QuestionIntent = Literal["numerical", "guidance", "both"]


@dataclass(frozen=True)
class QuestionRequest:
    """What the caller asked, plus the scope they claim (never inferred from free text alone)."""

    text: str
    country_code: str | None = None  # ISO 3166-1 alpha-2, explicitly supplied by the caller
    disease: str | None = None
    user_id: UUID | None = None  # for rag_feedback only; never sent to the model (spec rule 11)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("question text must not be blank")
        if len(self.text) > 2000:
            raise ValueError("question text exceeds the 2000-character limit")


@dataclass(frozen=True)
class StructuredObservation:
    """One row of structured evidence, already produced by the deterministic surveillance API --
    the workflow never recomputes a number, it only cites what app.surveillance.service returned.
    """

    geography_id: str
    geography_name: str
    geographic_resolution: str
    country_code: str
    disease: str
    indicator: str
    surveillance_stream: str | None
    period_start: date
    period_end: date
    value: float | None
    unit: str | None
    data_status: str
    quality_flags: tuple[str, ...]
    source_slug: str
    retrieved_at: datetime | None


@dataclass(frozen=True)
class DocumentEvidence:
    """One retrieved chunk, with everything needed to cite it (Prompt.txt §10's required fields)."""

    chunk_id: UUID
    document_id: UUID
    content: str
    similarity: float
    title: str
    publisher: str
    source_url: str
    document_type: str
    authority_level: str
    country_code: str | None
    disease: str | None
    published_at: datetime | None
    retrieved_at: datetime | None
    language: str
    review_status: str


@dataclass(frozen=True)
class StructuredSignal:
    """One row of app.surveillance.service.build_alerts()'s output -- the same deterministic
    "currently elevated" screening the public /surveillance/alerts endpoint returns. Lets the
    workflow answer "what's currently most prevalent / what's going around" without guessing:
    it only ever names a disease that the analysis engine has already flagged from real data,
    never one picked by the model. See StructuredDataSource.fetch_top_signals.
    """

    geography_id: str
    geography_name: str
    country_code: str
    disease: str
    indicator: str
    signal: Literal["elevated", "possible_anomaly"]
    period_start: date
    period_end: date
    source_slug: str


@dataclass(frozen=True)
class EvidenceBundle:
    """Steps C+D's combined output: everything step F is allowed to draw on."""

    structured: tuple[StructuredObservation, ...] = ()
    documents: tuple[DocumentEvidence, ...] = ()
    signals: tuple[StructuredSignal, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.structured and not self.documents and not self.signals


@dataclass(frozen=True)
class Citation:
    """One claim-to-evidence link the model's answer must be checkable against."""

    marker: str  # e.g. "[S1]" (structured), "[D1]" (document), or "[T1]" (trending signal)
    kind: Literal["structured", "document", "signal"]
    label: str  # human-readable: "PK sentinel influenza_positivity, 2026-10-05..2026-10-11"
    source_url: str | None
    retrieved_at: datetime | None


@dataclass(frozen=True)
class WorkflowAnswer:
    """Step H's output: answer text plus everything the spec requires alongside it."""

    answer_text: str
    citations: tuple[Citation, ...]
    reporting_periods: tuple[str, ...]
    last_update_timestamps: tuple[str, ...]
    limitations: tuple[str, ...]
    intent: QuestionIntent
    insufficient_evidence: bool
    workflow_version: str = WORKFLOW_VERSION
    model_identifier: str | None = None


@dataclass(frozen=True)
class CitationCheckResult:
    """Step G's verdict: does every factual claim in the draft answer have supporting evidence?"""

    all_supported: bool
    unsupported_markers: tuple[str, ...] = field(default_factory=tuple)
    unknown_markers: tuple[str, ...] = field(default_factory=tuple)  # cited but not in the bundle
