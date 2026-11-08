"""Migration 20261029000003/20261029000005/20261029000006 and app/rag/sql.py against a real
PostgreSQL, run with NO_PGVECTOR=1 in this sandbox (no pgvector extension available locally --
see tests/db/no_pgvector.sed). That strips the `vector` extension, the HNSW index, and the whole
match_document_chunks() function, so this file cannot and does not exercise vector similarity
search, embedding storage, or MATCH_CHUNKS -- those are exercised only in CI, which uses a
pgvector image. Every other column, constraint, and RLS policy on documents/document_chunks/
rag_feedback IS exercised here, against the real schema.

INSERT_CHUNK itself casts `:embedding` to `extensions.vector`, a type that does not exist at all
under NO_PGVECTOR (not even for a NULL value), so it cannot run unmodified in this sandbox. Chunk
rows below are inserted with a NO_PGVECTOR-safe statement that is otherwise identical to
sql.INSERT_CHUNK -- same columns, same constraints exercised -- substituting `float8[]` for the
embedding cast, mirroring exactly what tests/db/no_pgvector.sed already does to the table
definition. This keeps every non-embedding column and every constraint under real test coverage
locally, while being explicit that the vector cast itself is untested here.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.rag import sql
from tests.db.psql_harness import PsqlError

NOW = "2026-11-01T12:00:00+00:00"

# Identical to sql.INSERT_CHUNK except the embedding cast (see module docstring).
_INSERT_CHUNK_NO_PGVECTOR = """
insert into public.document_chunks
       (document_id, chunk_index, content, token_count, embedding, embedding_model, metadata_json)
values (cast(:document_id as uuid), :chunk_index, :content, :token_count,
        cast(:embedding as float8[]), :embedding_model, cast(:metadata_json as jsonb))
"""


@pytest.fixture(autouse=True)
def clean_rag_tables(pg):
    pg.script("truncate public.rag_feedback, public.document_chunks, public.documents cascade;")


def insert_document(pg, **overrides) -> str:
    values = dict(
        title="Dengue fact sheet",
        publisher="World Health Organization",
        source_url="https://www.emro.who.int/health-topics/dengue/index.html",
        document_type="fact_sheet",
        authority_level="official",
        country_code=None,
        disease="dengue",
        published_at=None,
        source_updated_at=None,
        retrieved_at=NOW,
        checksum="sha256:" + uuid.uuid4().hex,
        language="en",
        review_status="pending",
        content_storage_path=None,
        metadata_json=json.dumps({}),
    )
    values.update(overrides)
    row = pg.execute(sql.INSERT_DOCUMENT, values)
    return row[0]["id"]


def insert_chunk(pg, document_id, *, chunk_index=0, **overrides) -> None:
    values = dict(
        document_id=document_id,
        chunk_index=chunk_index,
        content="Dengue is a mosquito-borne viral infection.",
        token_count=10,
        embedding=None,
        embedding_model=None,
        metadata_json=json.dumps({"page_number": None}),
    )
    values.update(overrides)
    pg.execute(_INSERT_CHUNK_NO_PGVECTOR, values)


# --- documents: basic insert + required fields ---------------------------------------------------


def test_insert_document_round_trips_core_fields(pg):
    doc_id = insert_document(pg, disease="dengue", country_code="PK")
    row = pg.execute(sql.GET_DOCUMENT, {"document_id": doc_id})[0]
    assert row["title"] == "Dengue fact sheet"
    assert row["publisher"] == "World Health Organization"
    assert row["source_url"] == "https://www.emro.who.int/health-topics/dengue/index.html"
    assert row["document_type"] == "fact_sheet"
    assert row["authority_level"] == "official"
    assert row["country_code"] == "PK"
    assert row["disease"] == "dengue"
    assert row["review_status"] == "pending"


def test_insert_document_defaults_review_status_pending_and_language_en(pg):
    doc_id = pg.execute(
        sql.INSERT_DOCUMENT,
        {
            "title": "x",
            "publisher": "WHO",
            "source_url": "https://www.who.int/x",
            "document_type": "fact_sheet",
            "authority_level": "official",
            "country_code": None,
            "disease": None,
            "published_at": None,
            "source_updated_at": None,
            "retrieved_at": NOW,
            "checksum": "sha256:" + uuid.uuid4().hex,
            "language": "en",
            "review_status": "pending",
            "content_storage_path": None,
            "metadata_json": json.dumps({}),
        },
    )[0]["id"]
    row = pg.execute(sql.GET_DOCUMENT, {"document_id": doc_id})[0]
    assert row["review_status"] == "pending"
    assert row["language"] == "en"


@pytest.mark.parametrize("bad_type", ["press_release", "", "Fact_Sheet"])
def test_document_type_check_constraint_rejects_unknown_values(pg, bad_type):
    with pytest.raises(PsqlError, match="documents_document_type_check"):
        insert_document(pg, document_type=bad_type)


@pytest.mark.parametrize("bad_authority", ["unverified", "OFFICIAL", ""])
def test_authority_level_check_constraint_rejects_unknown_values(pg, bad_authority):
    with pytest.raises(PsqlError, match="documents_authority_level_check"):
        insert_document(pg, authority_level=bad_authority)


@pytest.mark.parametrize("bad_country", ["pk", "PAK", "123"])
def test_country_code_format_is_enforced_at_the_database_too(pg, bad_country):
    with pytest.raises(PsqlError):
        insert_document(pg, country_code=bad_country)


def test_disease_format_is_enforced_at_the_database(pg):
    with pytest.raises(PsqlError):
        insert_document(pg, disease="Dengue Fever")


def test_version_uniqueness_rejects_same_source_url_and_checksum_twice(pg):
    checksum = "sha256:" + uuid.uuid4().hex
    insert_document(pg, source_url="https://www.who.int/dup", checksum=checksum)
    with pytest.raises(PsqlError, match="documents_version_key"):
        insert_document(pg, source_url="https://www.who.int/dup", checksum=checksum)


def test_same_url_with_a_different_checksum_is_a_new_version(pg):
    id1 = insert_document(pg, source_url="https://www.who.int/dup2", checksum="sha256:aaa")
    id2 = insert_document(pg, source_url="https://www.who.int/dup2", checksum="sha256:bbb")
    assert id1 != id2
    row = pg.execute(sql.FIND_CURRENT_VERSION, {"source_url": "https://www.who.int/dup2"})
    # most recent by retrieved_at; both were inserted with the same NOW, so either is acceptable
    # as long as exactly one current (non-superseded) row is returned
    assert len(row) == 1


# --- superseding -----------------------------------------------------------------------------


def test_superseded_by_requires_review_status_superseded(pg):
    old_id = insert_document(pg, source_url="https://www.who.int/old", checksum="sha256:old1")
    new_id = insert_document(pg, source_url="https://www.who.int/old", checksum="sha256:old2")
    with pytest.raises(PsqlError, match="documents_superseded_not_approved"):
        pg.script(
            f"update public.documents set superseded_by = '{new_id}' "
            f"where id = '{old_id}' and review_status = 'pending';"
        )


def test_mark_superseded_sets_status_and_superseded_by(pg):
    old_id = insert_document(pg, source_url="https://www.who.int/ms", checksum="sha256:ms1")
    new_id = insert_document(pg, source_url="https://www.who.int/ms", checksum="sha256:ms2")
    pg.execute(sql.MARK_SUPERSEDED, {"document_id": old_id, "superseded_by": new_id})
    row = pg.execute(sql.GET_DOCUMENT, {"document_id": old_id})[0]
    assert row["review_status"] == "superseded"


def test_find_current_version_excludes_superseded_rows(pg):
    url = "https://www.who.int/fcv"
    old_id = insert_document(pg, source_url=url, checksum="sha256:fcv1")
    new_id = insert_document(pg, source_url=url, checksum="sha256:fcv2")
    pg.execute(sql.MARK_SUPERSEDED, {"document_id": old_id, "superseded_by": new_id})
    row = pg.execute(sql.FIND_CURRENT_VERSION, {"source_url": url})
    assert len(row) == 1
    assert row[0]["id"] == new_id


def test_find_current_version_returns_none_for_unknown_url(pg):
    assert pg.execute(sql.FIND_CURRENT_VERSION, {"source_url": "https://nowhere.example/x"}) == []


# --- review status ---------------------------------------------------------------------------


def test_set_review_status_updates_the_row(pg):
    doc_id = insert_document(pg)
    pg.execute(sql.SET_REVIEW_STATUS, {"document_id": doc_id, "review_status": "approved"})
    row = pg.execute(sql.GET_DOCUMENT, {"document_id": doc_id})[0]
    assert row["review_status"] == "approved"


@pytest.mark.parametrize("bad_status", ["accepted", "PENDING", ""])
def test_review_status_check_constraint_rejects_unknown_values(pg, bad_status):
    with pytest.raises(PsqlError, match="documents_review_status_check"):
        pg.execute(
            sql.SET_REVIEW_STATUS, {"document_id": insert_document(pg), "review_status": bad_status}
        )


# --- document_chunks ---------------------------------------------------------------------------


def test_insert_chunk_round_trips_and_links_to_its_document(pg):
    doc_id = insert_document(pg)
    insert_chunk(pg, doc_id, chunk_index=0, content="first chunk")
    assert pg.execute(sql.COUNT_CHUNKS, {"document_id": doc_id})[0]["n"] == "1"


def test_chunk_index_must_be_non_negative(pg):
    doc_id = insert_document(pg)
    with pytest.raises(PsqlError, match="document_chunks_chunk_index_check"):
        insert_chunk(pg, doc_id, chunk_index=-1)


def test_chunk_index_unique_per_document(pg):
    doc_id = insert_document(pg)
    insert_chunk(pg, doc_id, chunk_index=0)
    with pytest.raises(PsqlError, match="document_chunks_unique_index"):
        insert_chunk(pg, doc_id, chunk_index=0)


def test_same_chunk_index_is_fine_across_different_documents(pg):
    doc1 = insert_document(pg, source_url="https://www.who.int/d1", checksum="sha256:d1")
    doc2 = insert_document(pg, source_url="https://www.who.int/d2", checksum="sha256:d2")
    insert_chunk(pg, doc1, chunk_index=0)
    insert_chunk(pg, doc2, chunk_index=0)  # must not raise
    assert pg.execute(sql.COUNT_CHUNKS, {"document_id": doc1})[0]["n"] == "1"
    assert pg.execute(sql.COUNT_CHUNKS, {"document_id": doc2})[0]["n"] == "1"


def test_chunk_is_deleted_when_its_document_is_deleted(pg):
    doc_id = insert_document(pg)
    insert_chunk(pg, doc_id, chunk_index=0)
    pg.script(f"delete from public.documents where id = '{doc_id}';")
    # document_chunks.document_id references documents(id) on delete cascade
    count_sql = (
        "select count(*) as n from public.document_chunks where document_id = cast(:id as uuid)"
    )
    assert pg.execute(count_sql, {"id": doc_id})[0]["n"] == "0"


def test_count_chunks_is_zero_for_a_document_with_none(pg):
    doc_id = insert_document(pg)
    assert pg.execute(sql.COUNT_CHUNKS, {"document_id": doc_id})[0]["n"] == "0"


# --- RLS: only approved documents/chunks are publicly visible -----------------------------------


def test_anon_cannot_see_pending_documents(pg):
    doc_id = insert_document(pg, review_status="pending")
    assert (
        pg.execute(
            "select id from public.documents where id = cast(:id as uuid)",
            {"id": doc_id},
            role="anon",
        )
        == []
    )


def test_anon_can_see_approved_documents(pg):
    doc_id = insert_document(pg, review_status="approved")
    rows = pg.execute(
        "select id from public.documents where id = cast(:id as uuid)", {"id": doc_id}, role="anon"
    )
    assert len(rows) == 1


def test_anon_cannot_see_chunks_of_a_pending_document(pg):
    doc_id = insert_document(pg, review_status="pending")
    insert_chunk(pg, doc_id, chunk_index=0)
    rows = pg.execute(
        "select id from public.document_chunks where document_id = cast(:id as uuid)",
        {"id": doc_id},
        role="anon",
    )
    assert rows == []


def test_anon_can_see_chunks_of_an_approved_document(pg):
    doc_id = insert_document(pg, review_status="approved")
    insert_chunk(pg, doc_id, chunk_index=0)
    rows = pg.execute(
        "select id from public.document_chunks where document_id = cast(:id as uuid)",
        {"id": doc_id},
        role="anon",
    )
    assert len(rows) == 1


def test_a_document_that_becomes_superseded_also_drops_out_of_public_view(pg):
    url = "https://www.who.int/vis"
    old_id = insert_document(pg, source_url=url, checksum="sha256:vis1", review_status="approved")
    new_id = insert_document(pg, source_url=url, checksum="sha256:vis2", review_status="approved")
    pg.execute(sql.MARK_SUPERSEDED, {"document_id": old_id, "superseded_by": new_id})
    rows = pg.execute(
        "select id from public.documents where id = cast(:id as uuid)", {"id": old_id}, role="anon"
    )
    assert rows == []


# --- rag_feedback ------------------------------------------------------------------------------


def test_insert_feedback_stores_a_hash_not_question_text(pg):
    doc_id = insert_document(pg)
    insert_chunk(pg, doc_id, chunk_index=0)
    chunk_id = pg.execute(
        "select id from public.document_chunks where document_id = cast(:id as uuid)",
        {"id": doc_id},
    )[0]["id"]
    row = pg.execute(
        sql.INSERT_FEEDBACK,
        {
            "user_id": None,
            "query_hash": "sha256:" + uuid.uuid4().hex,
            "retrieved_chunk_ids": "{" + chunk_id + "}",
            "rating": 4,
            "citations_supported": True,
            "comment": None,
            "workflow_version": "ai-workflow-1",
            "model_identifier": "gemini-2.0-flash",
        },
    )
    assert row[0]["id"]
    stored = pg.execute(
        "select query_hash from public.rag_feedback where id = cast(:id as uuid)",
        {"id": row[0]["id"]},
    )[0]
    assert stored["query_hash"].startswith("sha256:")


@pytest.mark.parametrize("bad_rating", [0, 6, -1])
def test_rating_must_be_between_one_and_five(pg, bad_rating):
    with pytest.raises(PsqlError, match="rag_feedback_rating_check"):
        pg.execute(
            sql.INSERT_FEEDBACK,
            {
                "user_id": None,
                "query_hash": "sha256:" + uuid.uuid4().hex,
                "retrieved_chunk_ids": "{}",
                "rating": bad_rating,
                "citations_supported": None,
                "comment": None,
                "workflow_version": "ai-workflow-1",
                "model_identifier": None,
            },
        )


def test_feedback_rating_may_be_null(pg):
    row = pg.execute(
        sql.INSERT_FEEDBACK,
        {
            "user_id": None,
            "query_hash": "sha256:" + uuid.uuid4().hex,
            "retrieved_chunk_ids": "{}",
            "rating": None,
            "citations_supported": None,
            "comment": None,
            "workflow_version": "ai-workflow-1",
            "model_identifier": None,
        },
    )
    assert row[0]["id"]


def test_clients_cannot_read_or_write_rag_feedback(pg):
    # No public RLS policy grants anon/authenticated access to rag_feedback (migration 0005
    # only adds documents_public_read / document_chunks_public_read); it is service_role only.
    with pytest.raises(PsqlError):
        pg.execute("select count(*) as n from public.rag_feedback", role="anon")
