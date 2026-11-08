"""Wires the real adapters (surveillance DB, RagRepository, Gemini) into app.ai.workflow.

This is the only module that knows about SQLAlchemy connections, Settings, or Gemini
concretely; everything it builds is handed to app.ai.workflow.answer_question() through the
same Protocols tests use with fakes, so this module itself needs no tests of its own beyond the
construction logic covered by tests/ai/test_service.py (which stubs the DB connection).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.gemini import GeminiEmbeddingProvider, GeminiLLMProvider, LLMError
from app.ai.models import (
    DocumentEvidence,
    QuestionRequest,
    StructuredObservation,
    StructuredSignal,
    WorkflowAnswer,
)
from app.ai.workflow import answer_question
from app.api.v1.common import fetch
from app.config import Settings
from app.rag.sql_repository import SqlRagRepository
from app.surveillance import queries
from app.surveillance import service as surveillance_service

# Not a real external source: the engine-computed "currently flagged" screening
# (app.surveillance.service.build_alerts) has no single upstream source_slug of its own, since
# one alert can draw on observations from several ingested sources. This constant names the
# engine that produced the flag, not a data provider, and is never shown as if it were one.
_ALERTS_ENGINE_SLUG = "surveillance_alerts_engine"


class _DbStructuredDataSource:
    """StructuredDataSource backed by the same read-only query the public /observations
    endpoint uses (app.surveillance.queries.OBSERVATIONS) -- no new SQL, no new arithmetic.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def fetch_observations(
        self, *, country_code: str | None, disease: str | None, limit: int
    ) -> list[StructuredObservation]:
        rows = await fetch(
            self._conn,
            queries.OBSERVATIONS,
            {
                "country": country_code,
                "disease": disease,
                "indicator": None,
                "stream": None,
                "date_from": None,
                "date_to": None,
                "limit": limit,
                "offset": 0,
            },
        )
        return [_to_structured_observation(r) for r in rows]

    async def fetch_top_signals(
        self, *, country_code: str | None, limit: int
    ) -> list[StructuredSignal]:
        """Reuses app.surveillance.service.build_alerts() verbatim -- the exact same screening
        the public /surveillance/alerts endpoint returns -- so "what's currently most prevalent"
        is never answered from a notion of "trending" invented here.
        """
        rows = await fetch(
            self._conn,
            queries.LATEST_ANALYSES,
            {"country": country_code, "disease": None, "indicator": None, "stream": None},
        )
        now = datetime.now(UTC)
        alerts = surveillance_service.build_alerts(rows, today=now.date(), now=now)
        return [_to_structured_signal(a) for a in alerts.data[:limit]]


def _to_structured_signal(alert: Any) -> StructuredSignal:
    series = alert.series
    analysis = alert.analysis
    return StructuredSignal(
        geography_id=series.geography_id,
        geography_name=series.country_name,
        country_code=series.country_code,
        disease=series.disease,
        indicator=series.indicator,
        signal=alert.status,
        period_start=analysis.period_start,
        period_end=analysis.period_end,
        source_slug=_ALERTS_ENGINE_SLUG,
    )


def _to_structured_observation(row: Any) -> StructuredObservation:
    import json

    retrieved_at = row["retrieved_at"]
    if isinstance(retrieved_at, str):
        retrieved_at = datetime.fromisoformat(retrieved_at)
    quality_flags = row["quality_flags"]
    if isinstance(quality_flags, str):
        quality_flags = json.loads(quality_flags)
    return StructuredObservation(
        geography_id=str(row["geography_id"]),
        geography_name=row["country_name"],
        geographic_resolution=row["geographic_resolution"],
        country_code=row["country_code"],
        disease=row["disease"],
        indicator=row["indicator"],
        surveillance_stream=row["surveillance_stream"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        value=float(row["value"]) if row["value"] is not None else None,
        unit=row["unit"],
        data_status=row["data_status"],
        quality_flags=tuple(quality_flags or ()),
        source_slug=row["source_slug"],
        retrieved_at=retrieved_at,
    )


class _RagDocumentRetriever:
    """DocumentRetriever backed by SqlRagRepository.match_chunks (match_document_chunks()).
    Never exercised against a live pgvector-enabled database in this sandbox (see app.rag.sql).
    """

    def __init__(self, repository: SqlRagRepository) -> None:
        self._repository = repository

    async def retrieve(
        self,
        query_embedding: tuple[float, ...],
        *,
        country_code: str | None,
        disease: str | None,
        match_count: int,
    ) -> list[DocumentEvidence]:
        rows = await self._repository.match_chunks(
            query_embedding,
            match_count=match_count,
            country_code=country_code,
            disease=disease,
        )
        return [_to_document_evidence(r) for r in rows]


def _to_document_evidence(row: dict[str, Any]) -> DocumentEvidence:
    from uuid import UUID

    return DocumentEvidence(
        chunk_id=UUID(str(row["chunk_id"])),
        document_id=UUID(str(row["document_id"])),
        content=row["content"],
        similarity=float(row["similarity"]),
        title=row["title"],
        publisher=row["publisher"],
        source_url=row["source_url"],
        document_type=row["document_type"],
        authority_level=row["authority_level"],
        country_code=row["country_code"],
        disease=row["disease"],
        published_at=row["published_at"],
        retrieved_at=row["retrieved_at"],
        language=row["language"],
        review_status=row["review_status"],
    )


class AskService:
    """Entry point used by the API route and the CLI. Builds providers from Settings once per
    call (cheap: Gemini clients are thin HTTP wrappers) so there is no process-lifetime client
    to leak across requests.
    """

    def __init__(
        self, conn: AsyncConnection, repository: SqlRagRepository, settings: Settings
    ) -> None:
        self._structured = _DbStructuredDataSource(conn)
        self._retriever = _RagDocumentRetriever(repository)
        self._settings = settings

    def _build_providers(self) -> tuple[Any, Any]:
        """Returns (embedder, llm). Either may be unavailable (api key/model unset, or the
        google-genai package not installed) -- in which case the workflow returns a safe
        insufficient-evidence answer rather than raising (spec rule: "graceful provider-failure
        handling").
        """
        api_key = self._settings.gemini_api_key.get_secret_value()
        try:
            embedder = GeminiEmbeddingProvider(
                api_key, self._settings.gemini_embedding_model, self._settings.embedding_dimensions
            )
        except LLMError:
            embedder = None
        try:
            llm = GeminiLLMProvider(api_key, self._settings.gemini_model)
        except LLMError:
            llm = None
        return embedder, llm

    async def ask(self, request: QuestionRequest) -> WorkflowAnswer:
        embedder, llm = self._build_providers()
        # No embedding provider (Gemini not configured) means document retrieval cannot run.
        # Structured-data answers are still useful, so fall back to providers that report "no
        # documents found" rather than failing the whole request (spec: graceful provider failure).
        retriever = self._retriever if embedder is not None else _NullDocumentRetriever()
        return await answer_question(
            request,
            structured_source=self._structured,
            embedder=embedder or _NullEmbedder(),
            retriever=retriever,
            llm=llm,
        )


class _NullEmbedder:
    """Used only when Gemini is not configured; paired with _NullDocumentRetriever so no vector
    literal from an empty embedding is ever sent to the database.
    """

    model_name = "unconfigured"

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return ()


class _NullDocumentRetriever:
    async def retrieve(
        self, query_embedding: tuple[float, ...], *, country_code, disease, match_count
    ) -> list[DocumentEvidence]:
        return []
