"""Pure tests for app.rag.chunking: paragraph-aware splitting with page-number propagation."""

from __future__ import annotations

from app.rag.chunking import chunk_document, chunk_page_text
from app.rag.models import ChunkingConfig, ExtractedDocument, ExtractedPage, SourceKind

SMALL = ChunkingConfig(target_chars=50, overlap_chars=10, min_chunk_chars=5)


def test_short_text_is_a_single_chunk():
    chunks = chunk_page_text("one short paragraph", ChunkingConfig())
    assert chunks == ["one short paragraph"]


def test_splits_on_paragraph_boundaries_before_exceeding_target():
    text = "Paragraph one is short.\n\nParagraph two is also fairly short."
    chunks = chunk_page_text(
        text, ChunkingConfig(target_chars=30, overlap_chars=0, min_chunk_chars=1)
    )
    assert len(chunks) >= 2
    assert "Paragraph one is short." in chunks[0]


def test_never_produces_a_chunk_longer_than_target_for_short_sentences():
    text = "Short one. Short two. Short three. Short four. Short five."
    chunks = chunk_page_text(text, SMALL)
    for c in chunks:
        # allow a little slack: a single sentence longer than target_chars is hard-sliced, but
        # these sentences are all well under target_chars so no chunk should wildly exceed it.
        assert len(c) <= SMALL.target_chars + SMALL.overlap_chars + 20


def test_a_single_long_sentence_is_hard_sliced():
    long_sentence = "word " * 40  # ~200 chars, no sentence-ending punctuation
    # min_chunk_chars=1 so the trailing short slice isn't merged back into its predecessor --
    # that merge (tested separately below) is intentional and can legitimately exceed target_chars.
    chunks = chunk_page_text(
        long_sentence.strip(), ChunkingConfig(target_chars=60, overlap_chars=0, min_chunk_chars=1)
    )
    assert len(chunks) > 1
    assert all(len(c) <= 60 for c in chunks)


def test_overlap_carries_tail_of_previous_chunk_forward():
    text = "A" * 40 + ". " + "B" * 40 + ". " + "C" * 40 + "."
    cfg = ChunkingConfig(target_chars=45, overlap_chars=10, min_chunk_chars=1)
    chunks = chunk_page_text(text, cfg)
    assert len(chunks) >= 2
    # the second chunk should start with a tail of the first (the overlap), not from scratch
    assert chunks[1][:5] in chunks[0][-15:] or chunks[0][-10:][:5] in chunks[1]


def test_too_short_trailing_chunk_is_merged_into_predecessor():
    text = "First full paragraph with enough content to be its own chunk here.\n\nX"
    chunks = chunk_page_text(
        text, ChunkingConfig(target_chars=1000, overlap_chars=0, min_chunk_chars=5)
    )
    assert len(chunks) == 1
    assert chunks[0].endswith("X")


def test_blank_input_produces_no_chunks():
    assert chunk_page_text("   \n\n  ", ChunkingConfig()) == []


def test_chunk_document_assigns_global_chunk_index_across_pages():
    doc = ExtractedDocument(
        pages=(
            ExtractedPage(page_number=1, text="Page one text, short."),
            ExtractedPage(page_number=2, text="Page two text, also short."),
        )
    )
    chunks = chunk_document(doc, SourceKind.PDF, ChunkingConfig())
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_document_carries_page_number_for_pdf_sources():
    doc = ExtractedDocument(
        pages=(
            ExtractedPage(page_number=1, text="Page one text."),
            ExtractedPage(page_number=2, text="Page two text."),
        )
    )
    chunks = chunk_document(doc, SourceKind.PDF, ChunkingConfig())
    assert chunks[0].page_number == 1
    assert chunks[1].page_number == 2


def test_chunk_document_leaves_page_number_none_for_html_sources():
    doc = ExtractedDocument(pages=(ExtractedPage(page_number=1, text="HTML has no real pages."),))
    chunks = chunk_document(doc, SourceKind.HTML, ChunkingConfig())
    assert all(c.page_number is None for c in chunks)


def test_chunk_document_on_empty_document_returns_no_chunks():
    assert chunk_document(ExtractedDocument(pages=()), SourceKind.HTML, ChunkingConfig()) == []


def test_default_config_used_when_none_passed():
    doc = ExtractedDocument(pages=(ExtractedPage(page_number=1, text="hello world"),))
    chunks = chunk_document(doc, SourceKind.HTML)
    assert len(chunks) == 1
    assert chunks[0].content == "hello world"
