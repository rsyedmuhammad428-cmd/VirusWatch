"""Interfaces the ingestion pipeline depends on (mirrors app.ingestion.ports)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.rag.models import DocumentUpsert, ExtractedDocument, IngestOutcome, RawDocument, SourceSpec


class Downloader(Protocol):
    """Fetches a SourceSpec's URL. The real implementation (httpx) is never exercised in this
    sandbox (no outbound network from app code); app.rag.pipeline is tested against a fake.
    """

    async def fetch(self, source: SourceSpec) -> RawDocument: ...


class Extractor(Protocol):
    def extract(self, source: SourceSpec, raw: RawDocument) -> ExtractedDocument: ...


class Embedder(Protocol):
    """One call embeds a batch of chunk texts, in order. See app.ai.ports.EmbeddingPort for the
    Gemini-backed implementation shared with the retrieval side of Phase 5.
    """

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...

    @property
    def model_name(self) -> str: ...


class RagRepository(Protocol):
    async def find_current_version(self, source_url: str) -> tuple[UUID, str] | None:
        """(document_id, checksum) of the most recent non-superseded version at this URL, if any."""
        ...

    async def upsert_document(
        self, upsert: DocumentUpsert, *, supersedes: UUID | None
    ) -> IngestOutcome:
        """Insert a new document + its chunks in one transaction; mark `supersedes` superseded."""
        ...
