"""Pure validation tests for app.rag.models -- mirrors tests/ingestion/test_models.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.rag.models import (
    ChunkingConfig,
    DocumentMetadata,
    EmbeddedChunk,
    ExtractedDocument,
    ExtractedPage,
    TextChunk,
)

RETRIEVED = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _metadata(**overrides) -> DocumentMetadata:
    base = dict(
        title="Dengue fact sheet",
        publisher="WHO",
        source_url="https://www.who.int/example",
        document_type="fact_sheet",
        retrieved_at=RETRIEVED,
    )
    base.update(overrides)
    return DocumentMetadata(**base)


def test_valid_metadata_round_trips():
    m = _metadata()
    assert m.authority_level == "other"
    assert m.review_status == "pending"
    assert m.language == "en"


def test_blank_title_rejected():
    with pytest.raises(ValueError, match="title"):
        _metadata(title="   ")


def test_blank_publisher_rejected():
    with pytest.raises(ValueError, match="publisher"):
        _metadata(publisher="")


@pytest.mark.parametrize("url", ["www.who.int/example", "ftp://who.int", "not a url"])
def test_source_url_must_be_absolute_http(url):
    with pytest.raises(ValueError, match="source_url"):
        _metadata(source_url=url)


def test_homepage_style_url_is_still_accepted_by_the_model():
    # The model only checks "absolute URL"; "not a generic homepage" is a catalog-authoring
    # convention (app.rag.catalog), not something that can be mechanically validated here.
    m = _metadata(source_url="https://www.who.int/")
    assert m.source_url == "https://www.who.int/"


@pytest.mark.parametrize("code", ["usa", "U", "123", "us"])
def test_country_code_must_be_two_uppercase_letters(code):
    with pytest.raises(ValueError, match="country_code"):
        _metadata(country_code=code)


def test_country_code_none_is_allowed():
    assert _metadata(country_code=None).country_code is None


@pytest.mark.parametrize("disease", ["Dengue", "den-gue", "dengue fever", ""])
def test_disease_must_be_snake_case(disease):
    with pytest.raises(ValueError, match="disease"):
        _metadata(disease=disease)


def test_retrieved_at_cannot_precede_published_at():
    with pytest.raises(ValueError, match="retrieved_at precedes published_at"):
        _metadata(published_at=datetime(2026, 12, 1, tzinfo=UTC), retrieved_at=RETRIEVED)


def test_retrieved_at_equal_to_published_at_is_allowed():
    m = _metadata(published_at=RETRIEVED, retrieved_at=RETRIEVED)
    assert m.published_at == m.retrieved_at


def test_extracted_document_full_text_joins_nonempty_pages():
    doc = ExtractedDocument(
        pages=(
            ExtractedPage(page_number=1, text="first page"),
            ExtractedPage(page_number=2, text=""),
            ExtractedPage(page_number=3, text="third page"),
        )
    )
    assert doc.full_text == "first page\n\nthird page"
    assert not doc.is_empty


def test_extracted_document_with_only_blank_pages_is_empty():
    doc = ExtractedDocument(pages=(ExtractedPage(page_number=1, text="   "),))
    assert doc.is_empty


def test_extracted_document_with_no_pages_is_empty():
    assert ExtractedDocument(pages=()).is_empty


def test_chunking_config_rejects_non_positive_target():
    with pytest.raises(ValueError, match="target_chars"):
        ChunkingConfig(target_chars=0)


@pytest.mark.parametrize("overlap", [-1, 1200, 2000])
def test_chunking_config_rejects_out_of_range_overlap(overlap):
    with pytest.raises(ValueError, match="overlap_chars"):
        ChunkingConfig(target_chars=1200, overlap_chars=overlap)


def test_chunking_config_overlap_zero_is_allowed():
    cfg = ChunkingConfig(target_chars=1200, overlap_chars=0)
    assert cfg.overlap_chars == 0


def test_text_chunk_rejects_blank_content():
    with pytest.raises(ValueError, match="blank"):
        TextChunk(chunk_index=0, content="   ", page_number=None)


def test_text_chunk_rejects_negative_index():
    with pytest.raises(ValueError, match="chunk_index"):
        TextChunk(chunk_index=-1, content="hello", page_number=None)


def test_text_chunk_page_number_optional_for_html():
    chunk = TextChunk(chunk_index=0, content="hello", page_number=None)
    assert chunk.page_number is None


def test_embedded_chunk_rejects_empty_embedding():
    chunk = TextChunk(chunk_index=0, content="hello", page_number=None)
    with pytest.raises(ValueError, match="embedding"):
        EmbeddedChunk(chunk=chunk, embedding=(), embedding_model="gemini-embedding-001")


def test_embedded_chunk_rejects_blank_model_name():
    chunk = TextChunk(chunk_index=0, content="hello", page_number=None)
    with pytest.raises(ValueError, match="embedding_model"):
        EmbeddedChunk(chunk=chunk, embedding=(0.1, 0.2), embedding_model="  ")
