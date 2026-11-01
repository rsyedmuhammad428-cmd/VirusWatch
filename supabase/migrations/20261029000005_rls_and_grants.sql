-- 0005: Row Level Security and least-privilege grants.
--
-- Model:
--   * RLS is ENABLED on every table. It is never disabled to make queries easier.
--   * anon / authenticated start with NO privileges (Supabase grants broad defaults; we revoke).
--   * Public, approved aggregate data is readable by anon + authenticated through explicit policies.
--   * User-owned tables: a user can read only their own rows. Notification tables are read-only
--     for clients; all writes go through the backend, which validates consent and email ownership.
--   * Operational tables (ingestion_runs, job_runs, evaluation_runs, email_events, rag_feedback,
--     user_roles) have RLS enabled and NO client policies => invisible to clients.
--   * service_role (backend) bypasses RLS by design; keep that key server-side only.

alter table public.data_sources            enable row level security;
alter table public.ingestion_runs          enable row level security;
alter table public.geographies             enable row level security;
alter table public.observations            enable row level security;
alter table public.observation_revisions   enable row level security;
alter table public.analysis_results        enable row level security;
alter table public.documents               enable row level security;
alter table public.document_chunks         enable row level security;
alter table public.rag_feedback            enable row level security;
alter table public.evaluation_runs         enable row level security;
alter table public.job_runs                enable row level security;
alter table public.user_roles              enable row level security;
alter table public.profiles                enable row level security;
alter table public.notification_preferences enable row level security;
alter table public.notification_history    enable row level security;
alter table public.email_events            enable row level security;

-- Start from zero for client roles, including anything created later.
revoke all on all tables in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
grant all on all tables in schema public to service_role;

-- ------------------------------------------------------------- public, approved data
grant select on public.data_sources, public.geographies, public.observations,
                public.analysis_results, public.documents, public.document_chunks
  to anon, authenticated;

create policy data_sources_public_read on public.data_sources
  for select to anon, authenticated using (true);

create policy geographies_public_read on public.geographies
  for select to anon, authenticated using (true);

create policy observations_public_read on public.observations
  for select to anon, authenticated using (true);

-- Results awaiting or failing human review are not public.
create policy analysis_results_public_read on public.analysis_results
  for select to anon, authenticated using (review_status in ('auto', 'approved'));

create policy documents_public_read on public.documents
  for select to anon, authenticated using (review_status = 'approved');

create policy document_chunks_public_read on public.document_chunks
  for select to anon, authenticated
  using (exists (
    select 1 from public.documents d
    where d.id = document_chunks.document_id and d.review_status = 'approved'
  ));

-- ------------------------------------------------------------- user-owned data
grant select on public.profiles, public.notification_preferences, public.notification_history
  to authenticated;
-- Column-level UPDATE: clients may only edit presentational profile fields.
grant update (preferred_name, preferred_country, preferred_region, preferred_language, timezone)
  on public.profiles to authenticated;

create policy profiles_select_own on public.profiles
  for select to authenticated using (id = (select auth.uid()));

create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

create policy notification_preferences_select_own on public.notification_preferences
  for select to authenticated using (user_id = (select auth.uid()));

create policy notification_history_select_own on public.notification_history
  for select to authenticated using (user_id = (select auth.uid()));

-- Trigger function must not be callable as an RPC.
revoke execute on function public.handle_new_user() from public, anon, authenticated;
