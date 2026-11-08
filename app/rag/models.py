"""Canonical in-memory representation of RAG documents and chunks.

Mirrors the CHECK constraints on public.documents / public.document_chunks
(supabase/migrations/20261029000003_rag_and_ops.sql) so invalid combinations fail in Python,
with a clear message, before they ever reach the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

_IDENTIFIER = re.compile(r"^[a-z0-9_]+$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")

DocumentType = Literal["fact_sheet", "guidance", "bulletin", "scientific_paper", "other"]
AuthorityLevel = Literal["official", "peer_reviewed", "other"]
ReviewStatus = Literal["pending", "approved", "rejected", "superseded"]


class SourceKind(StrEnum):
    """How a document was obtained. Drives which extractor runs."""

    HTML = "html"
    PDF = "pdf"


@dataclass(frozen=True)
class DocumentMetadata:
    """Everything the spec (Prompt.txt §10) requires every chunk to preserve.

    Carried once on the document and copied onto every chunk's metadata_json at persist time,
    matching the documents/document_chunks schema comment "Copy of title/publisher/url/dates/
    disease/country/type/language/review_status at ingest time".
    """

    title: str
    publisher: str
    source_url: str  # the exact document URL, never a homepage (spec: "not a generic homepage")
    document_type: DocumentType
    authority_level: AuthorityLevel = "other"
    country_code: str | None = None  # ISO 3166-1 alpha-2; None = not country-specific
    disease: str | None = None
    published_at: datetime | None = None  # when the source says it was published
    source_updated_at: datetime | None = None  # when the source says it was last revised
    retrieved_at: datetime = field(kw_only=True)
    language: str = "en"
    review_status: ReviewStatus = "pending"

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("title must not be blank")
        if not self.publisher.strip():
            raise ValueError("publisher must not be blank")
        if not self.source_url.startswith(("http://", "https://")):
            raise ValueError(f"source_url must be an absolute URL, got {self.source_url!r}")
        if self.country_code is not None and not _COUNTRY.fullmatch(self.country_code):
            raise ValueError(f"country_code must be ISO 3166-1 alpha-2, got {self.country_code!r}")
        if self.disease is not None and not _IDENTIFIER.fullmatch(self.disease):
            raise ValueError(f"disease must be lowercase snake_case, got {self.disease!r}")
        if self.published_at is not None and self.retrieved_at < self.published_at:
            raise ValueError("retrieved_at precedes published_at")


@dataclass(frozen=True)
class RawDocument:
    """A downloaded or locally-supplied file, exactly as received (mirrors ingestion.Snapshot)."""

    content: bytes
    content_type: Literal["text/html", "application/pdf"]
    checksum: str  # "sha256:<hex>"
    retrieved_at: datetime  # timezone-aware, UTC


@dataclass(frozen=True)
class ExtractedPage:
    """One page of extracted text. page_number is 1-based; HTML documents have a single page."""

    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    """Plain text pulled out of a RawDocument, with navigation/boilerplate removed.

    Kept separate from DocumentChunk: extraction is "what text is there", chunking is
    "how we split it for retrieval" — two independently testable pure transforms.
    """

    pages: tuple[ExtractedPage, ...]
    dropped_boilerplate_chars: int = 0  # for observability only, never a correctness signal

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)

    @property
    def is_empty(self) -> bool:
        return not self.full_text.strip()


@dataclass(frozen=True)
class ChunkingConfig:
    target_chars: int = 1200  # no tokenizer dependency is installable here; chars approx tokens
    overlap_chars: int = 150
    min_chunk_chars: int = 80  # drop trailing fragments shorter than this (merge into previous)

    def __post_init__(self) -> None:
        if self.target_chars <= 0:
            raise ValueError("target_chars must be positive")
        if not (0 <= self.overlap_chars < self.target_chars):
            raise ValueError("overlap_chars must be >= 0 and less than target_chars")


@dataclass(frozen=True)
class TextChunk:
    """A semantically-coherent slice of ExtractedDocument text, before embedding."""

    chunk_index: int
    content: str
    page_number: int | None  # None for HTML sources; set for PDF sources (promprt2.txt)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("chunk content must not be blank")
        if self.chunk_index < 0:
            raise ValueError("chunk_index must be >= 0")


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: TextChunk
    embedding: tuple[float, ...]
    embedding_model: str

    def __post_init__(self) -> None:
        if not self.embedding:
            raise ValueError("embedding must not be empty")
        if not self.embedding_model.strip():
            raise ValueError("embedding_model must not be blank")


@dataclass(frozen=True)
class DocumentUpsert:
    """What the pipeline hands to the repository for one document version."""

    metadata: DocumentMetadata
    checksum: str
    content_storage_path: str | None
    chunks: tuple[EmbeddedChunk, ...]


@dataclass(frozen=True)
class IngestOutcome:
    document_id: object | None  # UUID once persisted
    status: Literal[
        "inserted", "updated_unchanged_checksum", "superseded_previous", "skipped_empty"
    ]
    chunks_written: int
    superseded_document_id: object | None = None


@dataclass(frozen=True)
class SourceSpec:
    """One entry in the explicit-URL catalog (app.rag.catalog) — "source discovery or explicit
    URL ingestion" (Prompt.txt §10, step 1). Every URL here was chosen by a person, not crawled.
    """

    slug: str
    url: str
    title: str
    publisher: str
    document_type: DocumentType
    authority_level: AuthorityLevel
    source_kind: SourceKind
    country_code: str | None = None
    disease: str | None = None
    language: str = "en"
    notes: str = ""
