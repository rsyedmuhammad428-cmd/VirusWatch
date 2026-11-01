-- 0002: core surveillance schema.
-- Design rules enforced here:
--   * typed columns for all core fields; JSONB only for source-specific metadata
--   * missing values are explicit (data_status = 'missing'), never silently NULL/zero
--   * idempotent ingestion via a natural-key UNIQUE constraint
--   * corrections are preserved in observation_revisions

-- ---------------------------------------------------------------- data_sources
create table public.data_sources (
  id                uuid primary key default gen_random_uuid(),
  slug              text not null unique check (slug ~ '^[a-z0-9_]+$'),
  name              text not null,
  publisher         text not null,
  source_url        text not null,
  source_type       text not null check (source_type in
                      ('api', 'file_download', 'pdf_bulletin', 'html_page', 'other')),
  update_cadence    text,                      -- free text; NULL until verified against the source
  enabled           boolean not null default false,
  -- Licensing/terms: nothing is assumed. NULL terms_verified_at means "not yet reviewed".
  license_name      text,
  license_url       text,
  terms_verified_at timestamptz,
  allow_public_export boolean not null default false,
  notes             text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create trigger data_sources_set_updated_at before update on public.data_sources
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------- ingestion_runs
create table public.ingestion_runs (
  id                  uuid primary key default gen_random_uuid(),
  data_source_id      uuid not null references public.data_sources(id) on delete restrict,
  started_at          timestamptz not null default now(),
  completed_at        timestamptz,
  status              text not null default 'running' check (status in
                        ('running', 'succeeded', 'partial', 'failed', 'skipped_unchanged')),
  triggered_by        text not null default 'manual' check (triggered_by in
                        ('manual', 'scheduled', 'backfill', 'test')),
  dry_run             boolean not null default false,
  records_downloaded  integer not null default 0 check (records_downloaded >= 0),
  records_inserted    integer not null default 0 check (records_inserted >= 0),
  records_updated     integer not null default 0 check (records_updated >= 0),
  records_rejected    integer not null default 0 check (records_rejected >= 0),
  checksum            text,
  source_published_at timestamptz,
  error_summary       text,
  metadata_json       jsonb not null default '{}'::jsonb,
  constraint ingestion_runs_completed_when_not_running
    check (status = 'running' or completed_at is not null),
  constraint ingestion_runs_time_order
    check (completed_at is null or completed_at >= started_at),
  constraint ingestion_runs_failure_has_reason
    check (status not in ('failed', 'partial') or error_summary is not null)
);
create index ingestion_runs_source_started_idx
  on public.ingestion_runs (data_source_id, started_at desc);
create index ingestion_runs_status_idx on public.ingestion_runs (status, started_at desc);
-- At most one live (non-dry-run) run per source: concurrent schedulers/retries cannot double-ingest.
create unique index ingestion_runs_one_running_per_source
  on public.ingestion_runs (data_source_id) where status = 'running' and not dry_run;

-- ---------------------------------------------------------------- geographies
create table public.geographies (
  id                   uuid primary key default gen_random_uuid(),
  country_code         text not null check (country_code ~ '^[A-Z]{2}$'),   -- ISO 3166-1 alpha-2
  country_name        text not null,
  admin_level          text not null check (admin_level in
                         ('country', 'admin1', 'admin2', 'city', 'region', 'site')),
  region_name          text,
  parent_geography_id  uuid references public.geographies(id) on delete restrict,
  -- Source codes are only meaningful within their coding system (e.g. a CDC HHS region id is
  -- not comparable to a WHO code). code_system makes that explicit.
  code_system          text,
  source_geography_code text,
  population           numeric check (population is null or population >= 0),
  population_year      smallint check (population_year is null or population_year between 1900 and 2100),
  population_source    text,
  metadata_json        jsonb not null default '{}'::jsonb,
  constraint geographies_no_self_parent check (parent_geography_id is distinct from id),
  constraint geographies_population_documented
    check (population is null or (population_year is not null and population_source is not null)),
  constraint geographies_country_has_no_region
    check (admin_level <> 'country' or region_name is null),
  constraint geographies_natural_key unique nulls not distinct
    (country_code, admin_level, region_name, code_system, source_geography_code)
);
create index geographies_country_level_idx on public.geographies (country_code, admin_level);
create index geographies_parent_idx on public.geographies (parent_geography_id);

-- ---------------------------------------------------------------- observations
create table public.observations (
  id                       uuid primary key default gen_random_uuid(),
  data_source_id           uuid not null references public.data_sources(id) on delete restrict,
  geography_id             uuid not null references public.geographies(id) on delete restrict,
  ingestion_run_id         uuid references public.ingestion_runs(id) on delete set null,
  -- Canonical, lowercase snake_case identifiers (e.g. 'influenza', 'influenza_a_h3n2').
  disease                  text not null check (disease ~ '^[a-z0-9_]+$'),
  pathogen                 text check (pathogen is null or pathogen ~ '^[a-z0-9_]+$'),
  indicator                text not null check (indicator ~ '^[a-z0-9_]+$'),
  -- Indicators of different types must never be compared as if equivalent (section 8).
  indicator_type           text not null check (indicator_type in
                             ('clinical_surveillance', 'laboratory_positivity', 'laboratory_count',
                              'wastewater', 'hospitalization', 'mortality', 'other')),
  reporting_period_start   date not null,
  reporting_period_end     date not null,
  published_at             timestamptz,           -- when the source published it (may be unknown)
  retrieved_at             timestamptz not null,  -- when we downloaded it
  value_numeric            numeric,
  value_text               text,
  numerator                numeric,
  denominator              numeric check (denominator is null or denominator >= 0),
  unit                     text,
  data_status              text not null default 'reported' check (data_status in
                             ('reported', 'provisional', 'revised', 'estimated', 'missing')),
  quality_flags            text[] not null default '{}',
  source_record_id         text,
  source_url               text not null,
  source_snapshot_checksum text,
  metadata_json            jsonb not null default '{}'::jsonb,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  constraint observations_period_order check (reporting_period_end >= reporting_period_start),
  -- Missing data must be declared as missing; a reported row must carry a value.
  constraint observations_value_present
    check (data_status = 'missing' or value_numeric is not null or value_text is not null),
  constraint observations_missing_has_no_value
    check (data_status <> 'missing' or (value_numeric is null and value_text is null)),
  constraint observations_numeric_has_unit
    check (value_numeric is null or unit is not null),
  -- Natural key for idempotent upserts. NULLS NOT DISTINCT so a NULL pathogen still dedupes.
  constraint observations_natural_key unique nulls not distinct
    (data_source_id, geography_id, disease, pathogen, indicator,
     reporting_period_start, reporting_period_end)
);
create index observations_geo_series_idx
  on public.observations (geography_id, disease, indicator, reporting_period_end desc);
create index observations_source_period_idx
  on public.observations (data_source_id, reporting_period_end desc);
create index observations_disease_period_idx
  on public.observations (disease, reporting_period_end desc);
create index observations_indicator_type_idx
  on public.observations (indicator_type, reporting_period_end desc);
create index observations_run_idx on public.observations (ingestion_run_id);
create trigger observations_set_updated_at before update on public.observations
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------- observation_revisions
-- Every time a re-ingestion changes a previously reported value, the old value is kept.
create table public.observation_revisions (
  id                              uuid primary key default gen_random_uuid(),
  observation_id                  uuid not null references public.observations(id) on delete cascade,
  revised_at                      timestamptz not null default now(),
  previous_value_numeric          numeric,
  previous_value_text             text,
  previous_numerator              numeric,
  previous_denominator            numeric,
  previous_unit                   text,
  previous_data_status            text,
  previous_retrieved_at           timestamptz,
  previous_snapshot_checksum      text,
  previous_ingestion_run_id       uuid,
  superseded_by_ingestion_run_id  uuid
);
create index observation_revisions_obs_idx
  on public.observation_revisions (observation_id, revised_at desc);

create or replace function public.log_observation_revision()
returns trigger
language plpgsql
as $$
begin
  if (old.value_numeric, old.value_text, old.numerator, old.denominator, old.unit, old.data_status)
     is distinct from
     (new.value_numeric, new.value_text, new.numerator, new.denominator, new.unit, new.data_status)
  then
    insert into public.observation_revisions (
      observation_id, previous_value_numeric, previous_value_text, previous_numerator,
      previous_denominator, previous_unit, previous_data_status, previous_retrieved_at,
      previous_snapshot_checksum, previous_ingestion_run_id, superseded_by_ingestion_run_id
    ) values (
      old.id, old.value_numeric, old.value_text, old.numerator,
      old.denominator, old.unit, old.data_status, old.retrieved_at,
      old.source_snapshot_checksum, old.ingestion_run_id, new.ingestion_run_id
    );
  end if;
  return new;
end;
$$;
create trigger observations_log_revision before update on public.observations
  for each row execute function public.log_observation_revision();

-- ---------------------------------------------------------------- analysis_results
create table public.analysis_results (
  id                       uuid primary key default gen_random_uuid(),
  geography_id             uuid not null references public.geographies(id) on delete restrict,
  disease                  text not null check (disease ~ '^[a-z0-9_]+$'),
  pathogen                 text check (pathogen is null or pathogen ~ '^[a-z0-9_]+$'),
  indicator                text not null check (indicator ~ '^[a-z0-9_]+$'),
  analysis_type            text not null check (analysis_type in
                             ('freshness', 'trend', 'rolling_average', 'baseline_comparison',
                              'seasonal_comparison', 'anomaly_detection', 'forecast')),
  analysis_period_start    date not null,
  analysis_period_end      date not null,
  baseline_period_start    date,
  baseline_period_end      date,
  latest_observation_end   date,
  observed_value           numeric,
  baseline_value           numeric,
  effect_size              numeric,
  score                    numeric,
  threshold                numeric,
  status                   text not null check (status in
                             ('normal', 'elevated', 'possible_anomaly', 'insufficient_data',
                              'stale_data', 'confirmed_by_source')),
  -- 'confirmed_by_source' is only allowed when a credible source URL backs it.
  source_confirmation_url  text,
  confidence_or_uncertainty jsonb not null default '{}'::jsonb,
  methodology_version      text not null,
  parameters               jsonb not null default '{}'::jsonb,
  missing_data_handling    text,
  limitations              text,
  input_observation_ids    uuid[] not null default '{}',
  input_hash               text not null,      -- hash of inputs + parameters: reproducibility key
  -- Human-approval workflow for high-impact alerts.
  review_status            text not null default 'auto' check (review_status in
                             ('auto', 'pending_review', 'approved', 'rejected')),
  explanation              text,
  created_at               timestamptz not null default now(),
  constraint analysis_period_order check (analysis_period_end >= analysis_period_start),
  constraint analysis_baseline_order
    check (baseline_period_end is null or baseline_period_start is null
           or baseline_period_end >= baseline_period_start),
  constraint analysis_confirmed_needs_source
    check (status <> 'confirmed_by_source' or source_confirmation_url is not null),
  constraint analysis_natural_key unique nulls not distinct
    (geography_id, disease, pathogen, indicator, analysis_type, methodology_version,
     analysis_period_end, input_hash)
);
create index analysis_results_series_idx
  on public.analysis_results (geography_id, disease, indicator, analysis_period_end desc);
create index analysis_results_flagged_idx
  on public.analysis_results (created_at desc) where status in ('elevated', 'possible_anomaly');
create index analysis_results_review_idx
  on public.analysis_results (review_status) where review_status = 'pending_review';
