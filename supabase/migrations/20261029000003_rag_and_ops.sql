-- 0003: RAG documents, evaluation, and job observability.
-- Embedding dimension: vector(768). Keep EMBEDDING_DIMENSIONS in .env identical. Changing the
-- embedding model to a different dimension requires a new migration and re-embedding.

create table public.documents (
  id                   uuid primary key default gen_random_uuid(),
  title                text not null,
  publisher            text not null,
  source_url           text not null,                -- exact document URL, not a homepage
  document_type        text not null check (document_type in
                         ('fact_sheet', 'guidance', 'bulletin', 'scientific_paper', 'other')),
  -- Separates official recommendations from research findings and everything else.
  authority_level      text not null default 'other' check (authority_level in
                         ('official', 'peer_reviewed', 'other')),
  country_code         text check (country_code is null or country_code ~ '^[A-Z]{2}$'),
  disease              text check (disease is null or disease ~ '^[a-z0-9_]+$'),
  published_at         timestamptz,
  source_updated_at    timestamptz,
  retrieved_at         timestamptz not null default now(),
  checksum             text not null,
  language             text not null default 'en',
  -- Only 'approved' documents are retrievable by the AI layer or visible to the public.
  review_status        text not null default 'pending' check (review_status in
                         ('pending', 'approved', 'rejected', 'superseded')),
  superseded_by        uuid references public.documents(id) on delete set null,
  content_storage_path text,
  metadata_json        jsonb not null default '{}'::jsonb,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  constraint documents_version_key unique (source_url, checksum),
  constraint documents_superseded_not_approved
    check (superseded_by is null or review_status = 'superseded')
);
create index documents_country_disease_idx on public.documents (country_code, disease);
create index documents_status_idx on public.documents (review_status);
create index documents_source_url_idx on public.documents (source_url);
create trigger documents_set_updated_at before update on public.documents
  for each row execute function public.set_updated_at();

create table public.document_chunks (
  id               uuid primary key default gen_random_uuid(),
  document_id      uuid not null references public.documents(id) on delete cascade,
  chunk_index      integer not null check (chunk_index >= 0),
  content          text not null,
  token_count      integer check (token_count is null or token_count >= 0),
  embedding        extensions.vector(768),
  embedding_model  text,
  -- Copy of title/publisher/url/dates/disease/country/type/language/review_status at ingest time.
  metadata_json    jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now(),
  constraint document_chunks_unique_index unique (document_id, chunk_index),
  constraint document_chunks_embedding_has_model
    check (embedding is null or embedding_model is not null)
);
create index document_chunks_embedding_hnsw_idx
  on public.document_chunks using hnsw (embedding extensions.vector_cosine_ops);

-- Retrieval feedback. Stores a hash of the question, not its text: questions can contain
-- personal health details we have no reason to keep.
create table public.rag_feedback (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid references auth.users(id) on delete set null,
  query_hash          text not null,
  retrieved_chunk_ids uuid[] not null default '{}',
  rating              smallint check (rating between 1 and 5),
  citations_supported boolean,
  comment             text,
  workflow_version    text,
  model_identifier    text,
  created_at          timestamptz not null default now()
);

create table public.evaluation_runs (
  id                  uuid primary key default gen_random_uuid(),
  run_type            text not null check (run_type in
                        ('detection', 'forecast', 'retrieval', 'citation')),
  methodology_version text not null,
  dataset_description text not null,
  parameters          jsonb not null default '{}'::jsonb,
  metrics             jsonb not null default '{}'::jsonb,
  n_events            integer check (n_events is null or n_events >= 0),
  limitations         text,
  code_version        text,
  created_at          timestamptz not null default now()
);

-- Generic scheduled-job log (analysis, digest dispatch, freshness monitoring, cleanup).
-- Source ingestion is tracked in ingestion_runs.
create table public.job_runs (
  id            uuid primary key default gen_random_uuid(),
  job_name      text not null,
  started_at    timestamptz not null default now(),
  completed_at  timestamptz,
  status        text not null default 'running' check (status in
                  ('running', 'succeeded', 'failed', 'skipped')),
  details       jsonb not null default '{}'::jsonb,
  error_summary text,
  constraint job_runs_completed_when_not_running
    check (status = 'running' or completed_at is not null)
);
create index job_runs_name_started_idx on public.job_runs (job_name, started_at desc);
