"""SQL used by RagRepository (bound parameters only; no string interpolation).

Checked against the repository's parameters by tests/test_sql_contract.py. Embedding-vector
inserts/updates are checked against a real PostgreSQL only in CI (pgvector image) -- the same,
already-documented limitation as match_document_chunks (see tests/db/no_pgvector.sed); local
tests/db/test_rag_sql.py exercises every other column and constraint with embedding left NULL.
"""

from __future__ import annotations

FIND_CURRENT_VERSION = """
select id::text as id, checksum
  from public.documents
 where source_url = :source_url
   and review_status != 'superseded'
 order by retrieved_at desc
 limit 1
"""

INSERT_DOCUMENT = """
insert into public.documents
       (title, publisher, source_url, document_type, authority_level, country_code, disease,
        published_at, source_updated_at, retrieved_at, checksum, language, review_status,
        content_storage_path, metadata_json)
values (:title, :publisher, :source_url, :document_type, :authority_level, :country_code, :disease,
        cast(:published_at as timestamptz), cast(:source_updated_at as timestamptz),
        cast(:retrieved_at as timestamptz), :checksum, :language, :review_status,
        :content_storage_path, cast(:metadata_json as jsonb))
returning id::text as id
"""

# embedding is bound as a pgvector literal string ("[0.1,0.2,...]") or SQL NULL, never a Python
# list -- asyncpg has no built-in vector codec, so the repository formats it before binding.
# Tests that cannot assume pgvector pass embedding=None (see module docstring).
INSERT_CHUNK = """
insert into public.document_chunks
       (document_id, chunk_index, content, token_count, embedding, embedding_model, metadata_json)
values (cast(:document_id as uuid), :chunk_index, :content, :token_count,
        cast(:embedding as extensions.vector), :embedding_model, cast(:metadata_json as jsonb))
"""

MARK_SUPERSEDED = """
update public.documents
   set review_status = 'superseded', superseded_by = cast(:superseded_by as uuid),
       updated_at = now()
 where id = cast(:document_id as uuid)
"""

GET_DOCUMENT = """
select id::text as id, title, publisher, source_url, document_type, authority_level,
       country_code, disease, published_at, retrieved_at, checksum, language, review_status
  from public.documents
 where id = cast(:document_id as uuid)
"""

COUNT_CHUNKS = """
select count(*) as n from public.document_chunks where document_id = cast(:document_id as uuid)
"""

SET_REVIEW_STATUS = """
update public.documents
   set review_status = :review_status, updated_at = now()
 where id = cast(:document_id as uuid)
"""

# Wraps match_document_chunks() (migration 20261029000006): cosine-similarity retrieval over
# approved chunks only (RLS; SECURITY INVOKER). Local tests cannot exercise this -- it, like the
# vector column and its index, is stripped under NO_PGVECTOR (tests/db/no_pgvector.sed) and is
# exercised only in CI, which uses a pgvector image. See app.ai.retrieval for the caller.
MATCH_CHUNKS = """
select * from public.match_document_chunks(
  query_embedding => cast(:query_embedding as extensions.vector),
  match_count     => :match_count,
  filter_country  => :filter_country,
  filter_disease  => :filter_disease
)
"""

INSERT_FEEDBACK = """
insert into public.rag_feedback
       (user_id, query_hash, retrieved_chunk_ids, rating, citations_supported, comment,
        workflow_version, model_identifier)
values (cast(:user_id as uuid), :query_hash, cast(:retrieved_chunk_ids as uuid[]), :rating,
        :citations_supported, :comment, :workflow_version, :model_identifier)
returning id::text as id
"""
