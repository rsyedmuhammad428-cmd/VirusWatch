"""In-memory fakes for app.rag.ports, mirroring tests/notifications/fakes.py's style."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.rag.models import (
    DocumentUpsert,
    ExtractedDocument,
    ExtractedPage,
    IngestOutcome,
    RawDocument,
    SourceSpec,
)


@dataclass
class FakeDownloader:
    content_by_url: dict[str, bytes]
    content_type_by_url: dict[str, str] = field(default_factory=dict)
    fail_urls: set[str] = field(default_factory=set)
    retrieved_at: object = None

    async def fetch(self, source: SourceSpec) -> RawDocument:
        if source.url in self.fail_urls:
            raise RuntimeError(f"simulated download failure for {source.url}")
        content = self.content_by_url[source.url]
        return RawDocument(
            content=content,
            content_type=self.content_type_by_url.get(source.url, "text/html"),  # type: ignore[arg-type]
            checksum=f"sha256:{hashlib.sha256(content).hexdigest()}",
            retrieved_at=self.retrieved_at,
        )


class FakeExtractor:
    """Returns the raw bytes decoded as text, one page -- real extraction is tested separately
    in tests/rag/test_extract.py; the pipeline only needs to know extraction happened."""

    def extract(self, source, raw) -> ExtractedDocument:
        text = raw.content.decode("utf-8")
        if not text.strip():
            return ExtractedDocument(pages=())
        return ExtractedDocument(pages=(ExtractedPage(page_number=1, text=text),))


@dataclass
class FakeEmbedder:
    dimensions: int = 8
    model_name_: str = "fake-embedding-model"
    calls: list[list[str]] = field(default_factory=list)

    @property
    def model_name(self) -> str:
        return self.model_name_

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        self.calls.append(texts)
        return [tuple(float(len(t) % 7 + i) for i in range(self.dimensions)) for t in texts]


@dataclass
class FakeRagRepository:
    by_url: dict[str, tuple[UUID, str]] = field(default_factory=dict)
    upserts: list[DocumentUpsert] = field(default_factory=list)
    superseded: list[UUID] = field(default_factory=list)

    async def find_current_version(self, source_url: str):
        return self.by_url.get(source_url)

    async def upsert_document(self, upsert: DocumentUpsert, *, supersedes):
        self.upserts.append(upsert)
        new_id = uuid4()
        if supersedes is not None:
            self.superseded.append(supersedes)
        self.by_url[upsert.metadata.source_url] = (new_id, upsert.checksum)
        return IngestOutcome(
            document_id=new_id,
            status="superseded_previous" if supersedes is not None else "inserted",
            chunks_written=len(upsert.chunks),
            superseded_document_id=supersedes,
        )
