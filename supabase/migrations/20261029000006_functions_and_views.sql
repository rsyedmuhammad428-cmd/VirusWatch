-- 0006: database functions and views.

-- Vector retrieval for RAG. SECURITY INVOKER: RLS applies, so callers only ever see chunks of
-- 'approved' documents. Documents with NULL country/disease are treated as globally applicable.
create or replace function public.match_document_chunks(
  query_embedding extensions.vector,
  match_count     integer default 8,
  filter_country  text    default null,
  filter_disease  text    default null
)
returns table (
  chunk_id        uuid,
  document_id     uuid,
  chunk_index     integer,
  content         text,
  similarity      double precision,
  title           text,
  publisher       text,
  source_url      text,
  document_type   text,
  authority_level text,
  country_code    text,
  disease         text,
  published_at    timestamptz,
  retrieved_at    timestamptz,
  language        text,
  review_status   text,
  metadata_json   jsonb
)
language sql
stable
security invoker
set search_path = public, extensions
as $$
  select c.id, c.document_id, c.chunk_index, c.content,
         1 - (c.embedding <=> query_embedding) as similarity,
         d.title, d.publisher, d.source_url, d.document_type, d.authority_level,
         d.country_code, d.disease, d.published_at, d.retrieved_at, d.language,
         d.review_status, c.metadata_json
  from public.document_chunks c
  join public.documents d on d.id = c.document_id
  where c.embedding is not null
    and d.review_status = 'approved'
    and (filter_country is null or d.country_code is null or d.country_code = filter_country)
    and (filter_disease is null or d.disease is null or d.disease = filter_disease)
  order by c.embedding <=> query_embedding
  limit least(greatest(match_count, 1), 50)
$$;

revoke all on function public.match_document_chunks(extensions.vector, integer, text, text)
  from public;
grant execute on function public.match_document_chunks(extensions.vector, integer, text, text)
  to anon, authenticated, service_role;

-- Backend-only freshness view for monitoring. Not granted to client roles because it exposes
-- operational run status.
create or replace view public.source_freshness
with (security_invoker = true) as
select
  s.id   as data_source_id,
  s.slug,
  s.name,
  s.enabled,
  s.update_cadence,
  (select max(r.completed_at) from public.ingestion_runs r
     where r.data_source_id = s.id and r.status in ('succeeded', 'partial'))
                                                          as last_successful_run_at,
  (select r.status from public.ingestion_runs r
     where r.data_source_id = s.id order by r.started_at desc limit 1)
                                                          as last_run_status,
  (select max(o.reporting_period_end) from public.observations o
     where o.data_source_id = s.id)                       as latest_reporting_period_end,
  (select max(o.published_at) from public.observations o
     where o.data_source_id = s.id)                       as latest_published_at,
  (select max(o.retrieved_at) from public.observations o
     where o.data_source_id = s.id)                       as latest_retrieved_at
from public.data_sources s;

revoke all on public.source_freshness from public, anon, authenticated;
grant select on public.source_freshness to service_role;
