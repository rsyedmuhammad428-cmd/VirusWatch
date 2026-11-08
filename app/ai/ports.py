"""Interfaces the AI workflow depends on. Production adapters (app.ai.gemini, app.rag.sql_repository
-backed retrieval) are never exercised against a live network or database in this sandbox; every
workflow test (tests/ai/test_workflow.py) runs against fakes implementing these Protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.ai.models import DocumentEvidence, QuestionRequest, StructuredObservation, StructuredSignal


@dataclass(frozen=True)
class GenerationRequest:
    system_prompt: str
    user_prompt: str
    max_output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class GenerationResult:
    text: str
    model_identifier: str
    truncated: bool = False


class LLMProvider(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Raise LLMError (app.ai.gemini) on any provider failure, including timeout."""
        ...


class EmbeddingProvider(Protocol):
    async def embed_query(self, text: str) -> tuple[float, ...]: ...

    @property
    def model_name(self) -> str: ...


class DocumentRetriever(Protocol):
    """Wraps the match_document_chunks() SQL function (app.rag). Filters are optional; a None
    filter means "don't restrict by that dimension", mirroring the SQL function's own semantics.
    """

    async def retrieve(
        self,
        query_embedding: tuple[float, ...],
        *,
        country_code: str | None,
        disease: str | None,
        match_count: int,
    ) -> list[DocumentEvidence]: ...


class StructuredDataSource(Protocol):
    """Wraps app.surveillance.service's read path. Never does its own arithmetic (spec rule 1)."""

    async def fetch_observations(
        self, *, country_code: str | None, disease: str | None, limit: int
    ) -> list[StructuredObservation]: ...

    async def fetch_top_signals(
        self, *, country_code: str | None, limit: int
    ) -> list[StructuredSignal]:
        """The same screening app.surveillance.service.build_alerts() returns for the public
        /surveillance/alerts endpoint (elevated/possible_anomaly, fresh data only), reused here
        so "what's currently most prevalent" is answered from that one deterministic source of
        truth rather than the model guessing or the workflow recomputing its own notion of
        "trending"."""
        ...


class FeedbackSink(Protocol):
    async def record(
        self, request: QuestionRequest, citation_check_passed: bool, model_identifier: str | None
    ) -> None: ...
