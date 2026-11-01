-- Database assertions. Run via scripts/test_db.sh after bootstrap.sql + all migrations.
-- Any failed assertion raises an exception and aborts the run (ON_ERROR_STOP).
--
-- Fixture rows are inserted into a DISPOSABLE test database only. They are clearly synthetic
-- ('example.test' URLs, 'fixture' markers) and must never be loaded into a real project.

\set ON_ERROR_STOP on
\o /dev/null

-- ---------------------------------------------------------------- helpers
create function pg_temp.expect_error(stmt text, expected text) returns void
language plpgsql as $$
declare got text;
begin
  begin
    execute stmt;
  exception when others then
    got := sqlstate;
    if got <> expected then
      raise exception 'expected SQLSTATE % but got % (%) for: %', expected, got, sqlerrm, stmt;
    end if;
    return;
  end;
  raise exception 'expected SQLSTATE % but statement succeeded: %', expected, stmt;
end $$;

create function pg_temp.expect_count(q text, expected bigint, label text) returns void
language plpgsql as $$
declare n bigint;
begin
  execute 'select count(*) from (' || q || ') s' into n;
  if n <> expected then
    raise exception '[%] expected % rows, got %', label, expected, n;
  end if;
end $$;

create function pg_temp.as_user(uid uuid) returns void language sql as $$
  select set_config('request.jwt.claims',
                    json_build_object('sub', uid, 'role', 'authenticated')::text, false)
$$;

create function pg_temp.no_user() returns void language sql as $$
  select set_config('request.jwt.claims', '', false)
$$;

-- ---------------------------------------------------------------- 1. structure + seeds
do $$
declare
  t text;
  r record;
  expected text[] := array[
    'data_sources','ingestion_runs','geographies','observations','observation_revisions',
    'analysis_results','documents','document_chunks','rag_feedback','evaluation_runs','job_runs',
    'user_roles','profiles','notification_preferences','notification_history','email_events'];
begin
  foreach t in array expected loop
    if to_regclass('public.' || t) is null then raise exception 'missing table %', t; end if;
  end loop;
  for r in
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity
  loop
    raise exception 'RLS is not enabled on public.%', r.relname;
  end loop;
end $$;

select pg_temp.expect_count('select 1 from public.data_sources', 4, 'seeded sources');
select pg_temp.expect_count('select 1 from public.data_sources where enabled', 0, 'sources start disabled');
select pg_temp.expect_count(
  'select 1 from public.data_sources
   where license_name is not null or terms_verified_at is not null or allow_public_export',
  0, 'no unverified licence claims seeded');
-- Only FluNet has a (documented-by-WHO) cadence so far; other sources must stay unverified.
select pg_temp.expect_count(
  'select 1 from public.data_sources where update_cadence is not null and slug <> ''who_flunet''',
  0, 'no unverified cadence claims for other sources');
select pg_temp.expect_count('select 1 from public.observations', 0, 'no observations are seeded');
select pg_temp.expect_count('select 1 from public.geographies where population is not null', 0,
                            'no invented populations');

select id as src from public.data_sources where slug = 'who_flunet' \gset
select id as geo from public.geographies where country_code = 'PK' and admin_level = 'country' \gset
select set_config('t.src', :'src', false);
select set_config('t.geo', :'geo', false);

-- ---------------------------------------------------------------- 2. data integrity
-- Reported row without any value
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    reporting_period_start, reporting_period_end, retrieved_at, source_url, data_status)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_x',
    'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/x', 'reported')
$q$, '23514');
-- Missing row that nevertheless carries a value
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    reporting_period_start, reporting_period_end, retrieved_at, source_url, data_status, value_numeric, unit)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_x',
    'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/x', 'missing', 0, 'percent')
$q$, '23514');
-- Numeric value without a unit
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_x',
    'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/x', 5)
$q$, '23514');
-- Period ends before it starts
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric, unit)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_x',
    'laboratory_positivity', '2026-01-11', '2026-01-05', now(), 'https://example.test/x', 5, 'percent')
$q$, '23514');
-- Non-canonical identifier
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric, unit)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'Influenza A', 'ind_x',
    'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/x', 5, 'percent')
$q$, '23514');

-- Missing data stays NULL (never coerced to zero)
insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
  reporting_period_start, reporting_period_end, retrieved_at, source_url, data_status)
values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_missing',
  'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/fixture', 'missing');
select pg_temp.expect_count(
  'select 1 from public.observations where indicator = ''ind_missing'' and value_numeric is null',
  1, 'missing value stays NULL');

-- Baseline observation, NULL pathogen
insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
  reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric, unit,
  source_snapshot_checksum)
values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_a',
  'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/fixture',
  12.5, 'percent', 'sha256:fixture-1');

-- Same period but a different surveillance stream is a different series, not a duplicate
insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
  surveillance_stream, reporting_period_start, reporting_period_end, retrieved_at, source_url,
  value_numeric, unit)
values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_a',
  'laboratory_positivity', 'sentinel', '2026-01-05', '2026-01-11', now(),
  'https://example.test/fixture', 3, 'percent');
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    surveillance_stream, reporting_period_start, reporting_period_end, retrieved_at, source_url,
    value_numeric, unit)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_a',
    'laboratory_positivity', 'sentinel', '2026-01-05', '2026-01-11', now(),
    'https://example.test/fixture', 4, 'percent')
$q$, '23505');   -- same stream + period + indicator is a duplicate
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    surveillance_stream, reporting_period_start, reporting_period_end, retrieved_at, source_url,
    value_numeric, unit)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_a',
    'laboratory_positivity', 'made_up', '2026-01-05', '2026-01-11', now(),
    'https://example.test/fixture', 4, 'percent')
$q$, '23514');   -- unknown stream value
delete from public.observations where surveillance_stream = 'sentinel';

-- Plain duplicate (NULL pathogen) must be rejected: proves NULLS NOT DISTINCT is effective
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric, unit)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_a',
    'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/fixture', 12.5, 'percent')
$q$, '23505');

-- Idempotent upsert, identical value: still one row, no revision recorded
insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
  reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric, unit,
  source_snapshot_checksum)
values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_a',
  'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/fixture',
  12.5, 'percent', 'sha256:fixture-1')
on conflict on constraint observations_natural_key do update
  set value_numeric = excluded.value_numeric, unit = excluded.unit,
      retrieved_at = excluded.retrieved_at, source_snapshot_checksum = excluded.source_snapshot_checksum;
select pg_temp.expect_count('select 1 from public.observations where indicator = ''ind_a''', 1,
                            'upsert keeps a single row');
select pg_temp.expect_count('select 1 from public.observation_revisions', 0,
                            'unchanged re-ingest creates no revision');

-- Source correction: value changes -> row updated in place, old value preserved
insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
  reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric, unit,
  source_snapshot_checksum, data_status)
values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'ind_a',
  'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/fixture',
  15.0, 'percent', 'sha256:fixture-2', 'revised')
on conflict on constraint observations_natural_key do update
  set value_numeric = excluded.value_numeric, unit = excluded.unit, data_status = excluded.data_status,
      retrieved_at = excluded.retrieved_at, source_snapshot_checksum = excluded.source_snapshot_checksum;
select pg_temp.expect_count('select 1 from public.observations where indicator = ''ind_a'' and value_numeric = 15.0',
                            1, 'correction applied in place');
select pg_temp.expect_count(
  'select 1 from public.observation_revisions
   where previous_value_numeric = 12.5 and previous_snapshot_checksum = ''sha256:fixture-1''',
  1, 'previous value preserved in revisions');

-- Geographies
select pg_temp.expect_error($q$
  insert into public.geographies (country_code, country_name, admin_level, population)
  values ('ZZ', 'Zedland', 'country', 1000)
$q$, '23514');   -- population needs a year and a source
select pg_temp.expect_error($q$
  insert into public.geographies (country_code, country_name, admin_level, code_system, source_geography_code)
  values ('PK', 'Pakistan', 'country', 'ISO3166-1-alpha2', 'PK')
$q$, '23505');   -- duplicate natural key
select pg_temp.expect_error($q$
  insert into public.geographies (country_code, country_name, admin_level, region_name)
  values ('PK', 'Pakistan', 'country', 'Sindh')
$q$, '23514');   -- a country row has no region

-- Ingestion runs
insert into public.ingestion_runs (data_source_id, status) values (current_setting('t.src')::uuid, 'running');
select pg_temp.expect_error($q$
  insert into public.ingestion_runs (data_source_id, status) values (current_setting('t.src')::uuid, 'running')
$q$, '23505');   -- only one live run per source
insert into public.ingestion_runs (data_source_id, status, dry_run)
  values (current_setting('t.src')::uuid, 'running', true);   -- dry runs may overlap
select pg_temp.expect_error($q$
  insert into public.ingestion_runs (data_source_id, status, completed_at)
  values (current_setting('t.src')::uuid, 'failed', now())
$q$, '23514');   -- failures must say why
update public.ingestion_runs set status = 'succeeded', completed_at = now()
  where data_source_id = current_setting('t.src')::uuid and not dry_run;
update public.ingestion_runs set status = 'succeeded', completed_at = now() where dry_run;

-- Analysis results
select pg_temp.expect_error(format($q$
  insert into public.analysis_results (geography_id, disease, indicator, analysis_type,
    analysis_period_start, analysis_period_end, status, methodology_version, input_hash)
  values (%L, 'influenza', 'ind_a', 'anomaly_detection', '2026-01-05', '2026-01-11',
    'confirmed_by_source', 'v1', 'h-confirm')
$q$, :'geo'), '23514');   -- confirmation requires a source URL

-- 0009: analysis results are stream-aware. Different streams coexist; an identical result does not.
insert into public.analysis_results (geography_id, disease, indicator, surveillance_stream,
  analysis_type, analysis_period_start, analysis_period_end, status, methodology_version, input_hash)
values (:'geo', 'influenza', 'ind_stream', 'sentinel', 'anomaly_detection',
        '2026-01-05', '2026-01-11', 'normal', 'v1', 'h-stream'),
       (:'geo', 'influenza', 'ind_stream', 'nonsentinel', 'anomaly_detection',
        '2026-01-05', '2026-01-11', 'normal', 'v1', 'h-stream');
select pg_temp.expect_error(format($q$
  insert into public.analysis_results (geography_id, disease, indicator, surveillance_stream,
    analysis_type, analysis_period_start, analysis_period_end, status, methodology_version, input_hash)
  values (%L, 'influenza', 'ind_stream', 'sentinel', 'anomaly_detection',
          '2026-01-05', '2026-01-11', 'normal', 'v1', 'h-stream')
$q$, :'geo'), '23505');   -- same series, same inputs: stored once
select pg_temp.expect_error(format($q$
  insert into public.analysis_results (geography_id, disease, indicator, surveillance_stream,
    analysis_type, analysis_period_start, analysis_period_end, status, methodology_version, input_hash)
  values (%L, 'influenza', 'ind_stream', 'bogus', 'anomaly_detection',
          '2026-01-05', '2026-01-11', 'normal', 'v1', 'h-bogus')
$q$, :'geo'), '23514');   -- stream must be a known value
-- a series without a stream concept (NULL) still dedupes (NULLS NOT DISTINCT)
insert into public.analysis_results (geography_id, disease, indicator, analysis_type,
  analysis_period_start, analysis_period_end, status, methodology_version, input_hash)
values (:'geo', 'influenza', 'ind_nostream', 'anomaly_detection',
        '2026-01-05', '2026-01-11', 'normal', 'v1', 'h-null');
select pg_temp.expect_error(format($q$
  insert into public.analysis_results (geography_id, disease, indicator, analysis_type,
    analysis_period_start, analysis_period_end, status, methodology_version, input_hash)
  values (%L, 'influenza', 'ind_nostream', 'anomaly_detection',
          '2026-01-05', '2026-01-11', 'normal', 'v1', 'h-null')
$q$, :'geo'), '23505');
delete from public.analysis_results where indicator in ('ind_stream', 'ind_nostream');

-- Documents / chunks
-- A superseded pointer on a document that is still 'approved' is contradictory
select pg_temp.expect_error($q$
  insert into public.documents (title, publisher, source_url, document_type, checksum, review_status, superseded_by)
  select 'x', 'x', 'https://example.test/x', 'guidance', 'c', 'approved', id from public.data_sources limit 1
$q$, '23514');
-- Same document URL + same checksum cannot be stored twice (re-ingesting is idempotent)
insert into public.documents (title, publisher, source_url, document_type, checksum)
  values ('dup', 'p', 'https://example.test/dup', 'other', 'same');
select pg_temp.expect_error($q$
  insert into public.documents (title, publisher, source_url, document_type, checksum)
  values ('dup', 'p', 'https://example.test/dup', 'other', 'same')
$q$, '23505');
delete from public.documents where source_url = 'https://example.test/dup';

-- Notification preferences consent rule (needs a user)
insert into auth.users (id, email, email_confirmed_at) values
  ('11111111-1111-1111-1111-111111111111', 'a@example.test', now()),
  ('22222222-2222-2222-2222-222222222222', 'b@example.test', now());
select pg_temp.expect_count('select 1 from public.profiles', 2, 'profiles auto-created by trigger');
select pg_temp.expect_error($q$
  insert into public.notification_preferences (user_id, email, enabled)
  values ('11111111-1111-1111-1111-111111111111', 'a@example.test', true)
$q$, '23514');   -- cannot enable without consent + verified email

-- ---------------------------------------------------------------- 3. RLS fixtures (superuser)
insert into public.analysis_results (geography_id, disease, indicator, analysis_type,
  analysis_period_start, analysis_period_end, status, methodology_version, input_hash, review_status)
values
  (current_setting('t.geo')::uuid, 'influenza', 'ind_a', 'anomaly_detection', '2026-01-05', '2026-01-11',
   'possible_anomaly', 'fixture-v1', 'h-auto', 'auto'),
  (current_setting('t.geo')::uuid, 'influenza', 'ind_a', 'anomaly_detection', '2026-01-05', '2026-01-11',
   'possible_anomaly', 'fixture-v1', 'h-approved', 'approved'),
  (current_setting('t.geo')::uuid, 'influenza', 'ind_a', 'anomaly_detection', '2026-01-05', '2026-01-11',
   'possible_anomaly', 'fixture-v1', 'h-pending', 'pending_review'),
  (current_setting('t.geo')::uuid, 'influenza', 'ind_a', 'anomaly_detection', '2026-01-05', '2026-01-11',
   'possible_anomaly', 'fixture-v1', 'h-rejected', 'rejected');

insert into public.documents (title, publisher, source_url, document_type, authority_level, checksum, review_status)
values
  ('Approved fixture doc', 'Fixture Publisher', 'https://example.test/doc-approved', 'guidance', 'official', 'c1', 'approved'),
  ('Pending fixture doc',  'Fixture Publisher', 'https://example.test/doc-pending',  'guidance', 'other',    'c2', 'pending');
insert into public.document_chunks (document_id, chunk_index, content)
select id, 0, 'chunk of ' || title from public.documents;

insert into public.notification_preferences (user_id, email, email_verified_at, consent_given_at, enabled)
values ('11111111-1111-1111-1111-111111111111', 'a@example.test', now(), now(), true),
       ('22222222-2222-2222-2222-222222222222', 'b@example.test', now(), now(), true);
insert into public.notification_history (user_id, notification_type, deduplication_key, subject)
values ('11111111-1111-1111-1111-111111111111', 'digest', 'digest:a:2026-W02', 'Digest for A'),
       ('22222222-2222-2222-2222-222222222222', 'digest', 'digest:b:2026-W02', 'Digest for B');
-- duplicate dedup key for the same user must be rejected
select pg_temp.expect_error($q$
  insert into public.notification_history (user_id, notification_type, deduplication_key, subject)
  values ('11111111-1111-1111-1111-111111111111', 'digest', 'digest:a:2026-W02', 'Duplicate')
$q$, '23505');
insert into public.user_roles (user_id, role) values ('11111111-1111-1111-1111-111111111111', 'admin');
insert into public.job_runs (job_name, status, completed_at) values ('fixture', 'succeeded', now());
insert into public.evaluation_runs (run_type, methodology_version, dataset_description)
  values ('detection', 'fixture-v1', 'fixture');
insert into public.email_events (webhook_event_id, event_type) values ('evt-1', 'email.delivered');
insert into public.rag_feedback (query_hash) values ('hash');

-- ---------------------------------------------------------------- 4. anon (public API role)
set role anon;
select pg_temp.no_user();
select pg_temp.expect_count('select 1 from public.data_sources', 4, 'anon reads sources');
select pg_temp.expect_count('select 1 from public.geographies', 2, 'anon reads geographies');
select pg_temp.expect_count('select 1 from public.observations', 2, 'anon reads observations');
select pg_temp.expect_count('select 1 from public.analysis_results', 2,
                            'anon sees only auto/approved analysis results');
select pg_temp.expect_count('select 1 from public.documents', 1, 'anon sees only approved documents');
select pg_temp.expect_count('select 1 from public.document_chunks', 1,
                            'anon sees only chunks of approved documents');
select pg_temp.expect_error('select 1 from public.profiles', '42501');
select pg_temp.expect_error('select 1 from public.notification_preferences', '42501');
select pg_temp.expect_error('select 1 from public.notification_history', '42501');
select pg_temp.expect_error('select 1 from public.user_roles', '42501');
select pg_temp.expect_error('select 1 from public.ingestion_runs', '42501');
select pg_temp.expect_error('select 1 from public.job_runs', '42501');
select pg_temp.expect_error('select 1 from public.evaluation_runs', '42501');
select pg_temp.expect_error('select 1 from public.email_events', '42501');
select pg_temp.expect_error('select 1 from public.rag_feedback', '42501');
select pg_temp.expect_error('select 1 from public.observation_revisions', '42501');
select pg_temp.expect_error('select 1 from public.source_freshness', '42501');
select pg_temp.expect_error('delete from public.observations', '42501');
select pg_temp.expect_error('update public.data_sources set enabled = true', '42501');
select pg_temp.expect_error($q$
  insert into public.observations (data_source_id, geography_id, disease, indicator, indicator_type,
    reporting_period_start, reporting_period_end, retrieved_at, source_url, value_numeric, unit)
  values (current_setting('t.src')::uuid, current_setting('t.geo')::uuid, 'influenza', 'forged',
    'laboratory_positivity', '2026-01-05', '2026-01-11', now(), 'https://example.test/forged', 99, 'percent')
$q$, '42501');
select pg_temp.expect_error('select public.handle_new_user()', '42501');
reset role;

-- ---------------------------------------------------------------- 5. authenticated: user A
set role authenticated;
select pg_temp.as_user('11111111-1111-1111-1111-111111111111');
select pg_temp.expect_count('select 1 from public.profiles', 1, 'A sees only own profile');
select pg_temp.expect_count('select 1 from public.profiles where id = ''22222222-2222-2222-2222-222222222222''',
                            0, 'A cannot see B profile');
select pg_temp.expect_count('select 1 from public.notification_preferences', 1, 'A sees only own prefs');
select pg_temp.expect_count('select 1 from public.notification_history', 1, 'A sees only own history');
select pg_temp.expect_count('select 1 from public.notification_history where subject = ''Digest for B''', 0,
                            'A cannot see B history');
select pg_temp.expect_count('select 1 from public.observations', 2, 'authenticated reads public data');
select pg_temp.expect_count('select 1 from public.documents', 1, 'authenticated sees approved docs only');
do $$
declare n integer;
begin
  update public.profiles set preferred_name = 'Alice' where id = '11111111-1111-1111-1111-111111111111';
  get diagnostics n = row_count;
  if n <> 1 then raise exception 'A should update own profile, rows=%', n; end if;
  update public.profiles set preferred_name = 'Hacked' where id = '22222222-2222-2222-2222-222222222222';
  get diagnostics n = row_count;
  if n <> 0 then raise exception 'A must not update B profile, rows=%', n; end if;
end $$;
select pg_temp.expect_error($q$update public.profiles set id = gen_random_uuid()$q$, '42501');
select pg_temp.expect_error($q$update public.profiles set created_at = now()$q$, '42501');
select pg_temp.expect_error($q$update public.notification_preferences set enabled = false$q$, '42501');
select pg_temp.expect_error($q$update public.notification_preferences set email_verified_at = now()$q$, '42501');
select pg_temp.expect_error($q$
  insert into public.notification_preferences (user_id, email)
  values ('11111111-1111-1111-1111-111111111111', 'x@example.test')$q$, '42501');
select pg_temp.expect_error($q$delete from public.notification_history$q$, '42501');
select pg_temp.expect_error('select 1 from public.user_roles', '42501');
select pg_temp.expect_error('select 1 from public.ingestion_runs', '42501');
select pg_temp.expect_error('select 1 from public.email_events', '42501');
select pg_temp.expect_error('select 1 from public.source_freshness', '42501');
select pg_temp.expect_error('delete from public.observations', '42501');

-- user B sees B's rows only
select pg_temp.as_user('22222222-2222-2222-2222-222222222222');
select pg_temp.expect_count('select 1 from public.notification_history where subject = ''Digest for B''', 1,
                            'B sees own history');
select pg_temp.expect_count('select 1 from public.notification_history where subject = ''Digest for A''', 0,
                            'B cannot see A history');
select pg_temp.expect_count('select 1 from public.notification_preferences where email = ''a@example.test''', 0,
                            'B cannot see A prefs');
-- authenticated with NO claims (no sub) sees no user rows at all
select pg_temp.no_user();
select pg_temp.expect_count('select 1 from public.profiles', 0, 'no uid => no profiles');
select pg_temp.expect_count('select 1 from public.notification_preferences', 0, 'no uid => no prefs');
reset role;

-- ---------------------------------------------------------------- 6. service_role (backend)
set role service_role;
select pg_temp.expect_count('select 1 from public.notification_preferences', 2, 'service role bypasses RLS');
select pg_temp.expect_count('select 1 from public.analysis_results', 4, 'service role sees all analyses');
select pg_temp.expect_count('select 1 from public.source_freshness', 4, 'service role reads freshness view');
select pg_temp.expect_count(
  'select 1 from public.source_freshness where slug = ''who_flunet'' and last_run_status = ''succeeded''
     and latest_reporting_period_end = date ''2026-01-11''', 1, 'freshness view reflects run + data');
insert into public.job_runs (job_name) values ('service-insert-ok');
reset role;

-- ---------------------------------------------------------------- 7. cascade on account deletion
delete from auth.users where id = '11111111-1111-1111-1111-111111111111';
select pg_temp.expect_count('select 1 from public.profiles where id = ''11111111-1111-1111-1111-111111111111''', 0,
                            'profile removed with account');
select pg_temp.expect_count('select 1 from public.notification_preferences where email = ''a@example.test''', 0,
                            'preferences removed with account');
select pg_temp.expect_count('select 1 from public.notification_history where subject = ''Digest for A''', 0,
                            'history removed with account');
select pg_temp.expect_count('select 1 from public.user_roles', 0, 'roles removed with account');

-- ---------------------------------------------------------------- 8. pgvector-specific (CI only)
select exists (select 1 from pg_extension where extname = 'vector') as has_vector \gset
\if :has_vector
  select pg_temp.expect_count(
    'select 1 from pg_indexes where indexname = ''document_chunks_embedding_hnsw_idx''', 1, 'hnsw index exists');
  insert into public.document_chunks (document_id, chunk_index, content, embedding, embedding_model)
  select id, 1, 'vector chunk one', ('[' || repeat('1,', 767) || '1]')::extensions.vector, 'fixture-model'
  from public.documents where source_url = 'https://example.test/doc-approved';
  insert into public.document_chunks (document_id, chunk_index, content, embedding, embedding_model)
  select id, 2, 'vector chunk two', ('[0,' || repeat('1,', 766) || '1]')::extensions.vector, 'fixture-model'
  from public.documents where source_url = 'https://example.test/doc-approved';
  insert into public.document_chunks (document_id, chunk_index, content, embedding, embedding_model)
  select id, 1, 'pending vector chunk', ('[' || repeat('1,', 767) || '1]')::extensions.vector, 'fixture-model'
  from public.documents where source_url = 'https://example.test/doc-pending';
  select pg_temp.expect_error($q$
    insert into public.document_chunks (document_id, chunk_index, content, embedding)
    select id, 9, 'no model', ('[' || repeat('1,', 767) || '1]')::extensions.vector
    from public.documents where source_url = 'https://example.test/doc-approved'
  $q$, '23514');
  set role anon;
  select pg_temp.no_user();
  select pg_temp.expect_count(
    $q$select 1 from public.match_document_chunks(('[' || repeat('1,', 767) || '1]')::extensions.vector, 10)$q$,
    2, 'retrieval returns only approved-document chunks with embeddings');
  select pg_temp.expect_count(
    $q$select 1 from public.match_document_chunks(('[' || repeat('1,', 767) || '1]')::extensions.vector, 10)
       where content = 'vector chunk one' and similarity > 0.99 and source_url = 'https://example.test/doc-approved'
         and title is not null and publisher is not null and retrieved_at is not null$q$,
    1, 'nearest chunk is exact match and carries citation metadata');
  select pg_temp.expect_count(
    $q$select 1 from public.match_document_chunks(('[' || repeat('1,', 767) || '1]')::extensions.vector, 10)
       where content like 'pending%'$q$,
    0, 'pending documents never retrievable');
  reset role;
\else
  \echo NOTE: pgvector not installed here; vector column/HNSW/match_document_chunks checks SKIPPED.
\endif

\o
\echo database checks complete
