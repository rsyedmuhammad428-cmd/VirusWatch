# Used ONLY by scripts/test_db.sh when NO_PGVECTOR=1 (a local Postgres without the pgvector
# extension). It swaps the vector column for float8[] and drops the HNSW index and the
# match_document_chunks function so the rest of the schema, constraints and RLS can still be
# verified. Those three vector-specific pieces are exercised in CI, which uses a pgvector image.
/create extension if not exists vector/d
s/extensions\.vector(768)/float8[]/g
/create index document_chunks_embedding_hnsw_idx/,/;/d
/create or replace function public\.match_document_chunks/,/^  to anon, authenticated, service_role;$/d
