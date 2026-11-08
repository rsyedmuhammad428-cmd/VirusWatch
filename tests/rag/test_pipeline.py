"""Tests for app.rag.pipeline against fakes (no network, no database, no Gemini)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.rag.models import ChunkingConfig, SourceKind, SourceSpec
from app.rag.pipeline import IngestionError, ingest_source, ingest_sources
from tests.rag.fakes import FakeDownloader, FakeEmbedder, FakeExtractor, FakeRagRepository

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _source(
    slug="who_dengue", url="https://www.who.int/dengue", kind=SourceKind.HTML
) -> SourceSpec:
    return SourceSpec(
        slug=slug,
        url=url,
        title="Dengue",
        publisher="WHO",
        document_type="fact_sheet",
        authority_level="official",
        source_kind=kind,
        disease="dengue",
    )


async def test_new_source_is_inserted_with_chunks_and_embeddings():
    source = _source()
    downloader = FakeDownloader(content_by_url={source.url: b"Paragraph one.\n\nParagraph two."})
    embedder = FakeEmbedder()
    repository = FakeRagRepository()

    outcome = await ingest_source(
        source,
        downloader=downloader,
        extractor=FakeExtractor(),
        embedder=embedder,
        repository=repository,
        now=NOW,
    )

    assert outcome.status == "inserted"
    assert outcome.chunks_written >= 1
    assert len(repository.upserts) == 1
    assert repository.upserts[0].metadata.review_status == "pending"
    assert embedder.calls  # embed() was actually invoked


async def test_unchanged_checksum_skips_reembedding():
    source = _source()
    content = b"Same content every time."
    downloader = FakeDownloader(content_by_url={source.url: content})
    embedder = FakeEmbedder()
    repository = FakeRagRepository()
    import hashlib
    import uuid

    existing_id = uuid.uuid4()
    repository.by_url[source.url] = (existing_id, f"sha256:{hashlib.sha256(content).hexdigest()}")

    outcome = await ingest_source(
        source,
        downloader=downloader,
        extractor=FakeExtractor(),
        embedder=embedder,
        repository=repository,
        now=NOW,
    )

    assert outcome.status == "updated_unchanged_checksum"
    assert outcome.document_id == existing_id
    assert outcome.chunks_written == 0
    assert not embedder.calls  # never re-embeds unchanged content
    assert not repository.upserts


async def test_changed_checksum_supersedes_previous_version():
    source = _source()
    downloader = FakeDownloader(content_by_url={source.url: b"New content this time."})
    repository = FakeRagRepository()
    import uuid

    old_id = uuid.uuid4()
    repository.by_url[source.url] = (old_id, "sha256:stale-checksum")

    outcome = await ingest_source(
        source,
        downloader=downloader,
        extractor=FakeExtractor(),
        embedder=FakeEmbedder(),
        repository=repository,
        now=NOW,
    )

    assert outcome.status == "superseded_previous"
    assert outcome.superseded_document_id == old_id
    assert old_id in repository.superseded


async def test_empty_extraction_is_skipped_without_writing_anything():
    source = _source()
    downloader = FakeDownloader(content_by_url={source.url: b"   "})
    repository = FakeRagRepository()

    outcome = await ingest_source(
        source,
        downloader=downloader,
        extractor=FakeExtractor(),
        embedder=FakeEmbedder(),
        repository=repository,
        now=NOW,
    )

    assert outcome.status == "skipped_empty"
    assert outcome.document_id is None
    assert not repository.upserts


async def test_embedder_vector_count_mismatch_raises_ingestion_error():
    source = _source()
    downloader = FakeDownloader(content_by_url={source.url: b"Some real paragraph of text."})

    class BrokenEmbedder(FakeEmbedder):
        async def embed(self, texts):
            return []  # wrong count

    with pytest.raises(IngestionError, match="vectors"):
        await ingest_source(
            source,
            downloader=downloader,
            extractor=FakeExtractor(),
            embedder=BrokenEmbedder(),
            repository=FakeRagRepository(),
            now=NOW,
        )


async def test_pdf_source_chunks_carry_page_numbers_through_the_pipeline():
    source = _source(kind=SourceKind.PDF)
    downloader = FakeDownloader(
        content_by_url={source.url: b"Page content text, long enough to form a chunk on its own."},
        content_type_by_url={source.url: "application/pdf"},
    )
    repository = FakeRagRepository()

    await ingest_source(
        source,
        downloader=downloader,
        extractor=FakeExtractor(),
        embedder=FakeEmbedder(),
        repository=repository,
        now=NOW,
        chunking_config=ChunkingConfig(),
    )

    chunks = repository.upserts[0].chunks
    assert chunks
    assert all(c.chunk.page_number == 1 for c in chunks)


async def test_ingest_sources_isolates_one_sources_failure_from_the_rest():
    good = _source(slug="good", url="https://www.who.int/good")
    bad = _source(slug="bad", url="https://www.who.int/bad")
    downloader = FakeDownloader(
        content_by_url={good.url: b"Fine content here."}, fail_urls={bad.url}
    )
    repository = FakeRagRepository()

    report = await ingest_sources(
        [good, bad],
        downloader=downloader,
        extractor=FakeExtractor(),
        embedder=FakeEmbedder(),
        repository=repository,
        now=NOW,
    )

    assert report.succeeded == 1
    assert report.failed == 1
    by_slug = {r.slug: r for r in report.results}
    assert by_slug["good"].outcome is not None
    assert by_slug["bad"].error is not None
    assert "simulated download failure" in by_slug["bad"].error


async def test_ingestion_report_chunks_written_sums_successful_sources_only():
    good = _source(slug="good", url="https://www.who.int/good")
    bad = _source(slug="bad", url="https://www.who.int/bad")
    downloader = FakeDownloader(
        content_by_url={good.url: b"Content paragraph one.\n\nContent paragraph two."},
        fail_urls={bad.url},
    )
    report = await ingest_sources(
        [good, bad],
        downloader=downloader,
        extractor=FakeExtractor(),
        embedder=FakeEmbedder(),
        repository=FakeRagRepository(),
        now=NOW,
    )
    assert report.chunks_written >= 1
