"""Orchestrates one source through discovery -> extraction -> chunking -> embedding -> storage.

Pure orchestration over the Protocols in ports.py, so it is fully testable with fakes
(tests/rag/test_pipeline.py) without any network, database, or Gemini access -- the same shape
as app.ingestion's runner and app.notifications' dispatcher.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from app.rag.chunking import chunk_document
from app.rag.extract import extract_html, extract_pdf
from app.rag.models import (
    ChunkingConfig,
    DocumentMetadata,
    DocumentUpsert,
    EmbeddedChunk,
    ExtractedDocument,
    IngestOutcome,
    RawDocument,
    SourceKind,
    SourceSpec,
)
from app.rag.ports import Downloader, Embedder, Extractor, RagRepository


class DefaultExtractor:
    """Extractor dispatching on RawDocument.content_type -- extract_html for text/html,
    extract_pdf (pdftotext -layout) for application/pdf.
    """

    def extract(self, source: SourceSpec, raw: RawDocument) -> ExtractedDocument:
        if raw.content_type == "application/pdf" or source.source_kind is SourceKind.PDF:
            return extract_pdf(raw.content)
        return extract_html(raw.content.decode("utf-8", errors="replace"))


class IngestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceOutcome:
    slug: str
    outcome: IngestOutcome | None
    error: str | None = None


@dataclass(frozen=True)
class IngestionReport:
    results: tuple[SourceOutcome, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.outcome is not None)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def chunks_written(self) -> int:
        return sum(r.outcome.chunks_written for r in self.results if r.outcome is not None)


def _checksum(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


async def ingest_source(
    source: SourceSpec,
    *,
    downloader: Downloader,
    extractor: Extractor,
    embedder: Embedder,
    repository: RagRepository,
    now: datetime,
    chunking_config: ChunkingConfig | None = None,
) -> IngestOutcome:
    """One source, end to end. Raises IngestionError; never writes a partial document."""
    raw = await downloader.fetch(source)
    checksum = _checksum(raw.content)

    current = await repository.find_current_version(source.url)
    if current is not None:
        current_id, current_checksum = current
        if current_checksum == checksum:
            return IngestOutcome(
                document_id=current_id, status="updated_unchanged_checksum", chunks_written=0
            )

    extracted = extractor.extract(source, raw)
    if extracted.is_empty:
        return IngestOutcome(document_id=None, status="skipped_empty", chunks_written=0)

    text_chunks = chunk_document(extracted, source.source_kind, chunking_config)
    if not text_chunks:
        return IngestOutcome(document_id=None, status="skipped_empty", chunks_written=0)

    vectors = await embedder.embed([c.content for c in text_chunks])
    if len(vectors) != len(text_chunks):
        raise IngestionError(
            f"embedder returned {len(vectors)} vectors for {len(text_chunks)} chunks"
        )
    embedded = tuple(
        EmbeddedChunk(chunk=c, embedding=v, embedding_model=embedder.model_name)
        for c, v in zip(text_chunks, vectors, strict=True)
    )

    metadata = DocumentMetadata(
        title=source.title,
        publisher=source.publisher,
        source_url=source.url,
        document_type=source.document_type,
        authority_level=source.authority_level,
        country_code=source.country_code,
        disease=source.disease,
        retrieved_at=now,
        language=source.language,
        review_status="pending",  # a human approves before the AI layer can retrieve it (RLS)
    )
    upsert = DocumentUpsert(
        metadata=metadata, checksum=checksum, content_storage_path=None, chunks=embedded
    )
    supersedes = current[0] if current is not None else None
    return await repository.upsert_document(upsert, supersedes=supersedes)


async def ingest_sources(
    sources: list[SourceSpec],
    *,
    downloader: Downloader,
    extractor: Extractor,
    embedder: Embedder,
    repository: RagRepository,
    now: datetime,
    chunking_config: ChunkingConfig | None = None,
) -> IngestionReport:
    """Each source succeeds or fails independently; one bad source never blocks the rest."""
    results: list[SourceOutcome] = []
    for source in sources:
        try:
            outcome = await ingest_source(
                source,
                downloader=downloader,
                extractor=extractor,
                embedder=embedder,
                repository=repository,
                now=now,
                chunking_config=chunking_config,
            )
            results.append(SourceOutcome(slug=source.slug, outcome=outcome))
        except Exception as exc:  # noqa: BLE001 - one source's failure must not abort the batch
            results.append(SourceOutcome(slug=source.slug, outcome=None, error=str(exc)))
    return IngestionReport(results=tuple(results))
