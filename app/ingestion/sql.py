"""SQL for ingestion. Parameters use SQLAlchemy's `:name` style and explicit casts.

Kept free of driver imports so the exact same statements can be executed against a real
PostgreSQL in tests/db (see tests/db/psql_harness.py). Do not put `:` characters inside SQL
string literals here: the test harness substitutes parameters textually.
"""

GET_SOURCE = """
select id, slug, name, enabled
from public.data_sources
where slug = :slug
"""

FAIL_STALE_RUNS = """
update public.ingestion_runs
   set status = 'failed',
       completed_at = now(),
       error_summary = 'Abandoned: no completion recorded in time (process likely stopped).'
 where data_source_id = cast(:source_id as uuid)
   and status = 'running'
   and started_at < now() - make_interval(secs => cast(:max_age_seconds as double precision))
"""

START_RUN = """
insert into public.ingestion_runs (data_source_id, status, triggered_by, dry_run, metadata_json)
values (cast(:source_id as uuid), 'running', :triggered_by, cast(:dry_run as boolean),
        cast(:metadata as jsonb))
returning id
"""

LAST_DATA_RUN = """
select status, checksum, metadata_json ->> 'signature' as signature
from public.ingestion_runs
where data_source_id = cast(:source_id as uuid)
  and status in ('succeeded', 'partial')
  and not dry_run
order by started_at desc
limit 1
"""

FINISH_RUN = """
update public.ingestion_runs
   set status = :status,
       completed_at = now(),
       records_downloaded = cast(:records_downloaded as integer),
       records_inserted = cast(:records_inserted as integer),
       records_updated = cast(:records_updated as integer),
       records_rejected = cast(:records_rejected as integer),
       checksum = :checksum,
       source_published_at = cast(:source_published_at as timestamptz),
       error_summary = :error_summary,
       metadata_json = metadata_json || cast(:metadata as jsonb)
 where id = cast(:run_id as uuid)
   and status = 'running'
"""

ENSURE_COUNTRY_GEOGRAPHY = """
insert into public.geographies
  (country_code, country_name, admin_level, code_system, source_geography_code)
values (:country_code, :country_name, 'country', 'ISO3166-1-alpha2', :country_code)
on conflict on constraint geographies_natural_key do nothing
"""

# One statement per batch. Rows whose country has no geography row are dropped by the inner join,
# so the caller MUST compare inserted + existing against the batch size.
#
# On conflict the value-bearing columns are refreshed. data_status becomes 'revised' only when a
# value actually changed (and the new value is not a blank). The BEFORE UPDATE trigger
# log_observation_revision() preserves the previous value in observation_revisions.
UPSERT_OBSERVATIONS = """
with incoming as (
  select r.*
  from jsonb_to_recordset(cast(:rows as jsonb)) as r(
    country_code text, disease text, pathogen text, indicator text, indicator_type text,
    surveillance_stream text, reporting_period_start date, reporting_period_end date,
    value_numeric numeric, numerator numeric, denominator numeric, unit text,
    data_status text, quality_flags jsonb, source_record_id text, source_url text,
    metadata_json jsonb
  )
),
upserted as (
  insert into public.observations as o (
    data_source_id, geography_id, ingestion_run_id, disease, pathogen, indicator, indicator_type,
    surveillance_stream, reporting_period_start, reporting_period_end, retrieved_at,
    value_numeric, numerator, denominator, unit, data_status, quality_flags,
    source_record_id, source_url, source_snapshot_checksum, metadata_json
  )
  select cast(:source_id as uuid), g.id, cast(:run_id as uuid), r.disease, r.pathogen, r.indicator,
         r.indicator_type, r.surveillance_stream, r.reporting_period_start, r.reporting_period_end,
         cast(:retrieved_at as timestamptz), r.value_numeric, r.numerator, r.denominator, r.unit,
         r.data_status,
         coalesce(array(select jsonb_array_elements_text(r.quality_flags)), '{}'::text[]),
         r.source_record_id, r.source_url, cast(:checksum as text),
         coalesce(r.metadata_json, '{}'::jsonb)
  from incoming r
  join public.geographies g
    on g.country_code = r.country_code
   and g.admin_level = 'country'
   and g.code_system = 'ISO3166-1-alpha2'
   and g.source_geography_code = r.country_code
  on conflict on constraint observations_natural_key do update set
    ingestion_run_id         = excluded.ingestion_run_id,
    retrieved_at             = excluded.retrieved_at,
    source_snapshot_checksum = excluded.source_snapshot_checksum,
    value_numeric            = excluded.value_numeric,
    numerator                = excluded.numerator,
    denominator              = excluded.denominator,
    unit                     = excluded.unit,
    quality_flags            = excluded.quality_flags,
    source_record_id         = excluded.source_record_id,
    source_url               = excluded.source_url,
    metadata_json            = excluded.metadata_json,
    data_status = case
      when excluded.data_status = 'missing' then 'missing'
      when (o.value_numeric, o.numerator, o.denominator, o.unit)
           is not distinct from
           (excluded.value_numeric, excluded.numerator, excluded.denominator, excluded.unit)
        then o.data_status
      else 'revised'
    end
  returning (xmax = 0) as was_inserted
)
select count(*) filter (where was_inserted)     as inserted,
       count(*) filter (where not was_inserted) as existing
from upserted
"""

COUNT_REVISIONS_FOR_RUN = """
select count(*) as revised
from public.observation_revisions
where superseded_by_ingestion_run_id = cast(:run_id as uuid)
"""
