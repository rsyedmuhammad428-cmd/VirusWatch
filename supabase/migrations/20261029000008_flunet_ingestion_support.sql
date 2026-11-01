-- 0008: support for the first real connector (WHO FluNet).
--
-- 1. surveillance_stream: FluNet reports SENTINEL, NONSENTINEL and NOTDEFINED streams for the
--    same country-week. They have different sampling designs, so they must be stored as separate
--    series (never silently summed) and therefore belong in the observation's natural key.
-- 2. who_flunet metadata: only facts confirmed on WHO's FluNet page are recorded.

alter table public.observations
  add column surveillance_stream text
    check (surveillance_stream is null
           or surveillance_stream in ('sentinel', 'nonsentinel', 'undefined'));

comment on column public.observations.surveillance_stream is
  'Sampling stream the value comes from (FluNet ORIGIN_SOURCE). NULL when the source has no such concept. Streams are separate series and are never summed implicitly.';

alter table public.observations drop constraint observations_natural_key;
alter table public.observations
  add constraint observations_natural_key unique nulls not distinct
    (data_source_id, geography_id, disease, pathogen, indicator,
     reporting_period_start, reporting_period_end, surveillance_stream);

create index observations_stream_idx
  on public.observations (geography_id, disease, indicator, surveillance_stream, reporting_period_end desc);

-- Operational: lets the runner find the latest snapshot per source cheaply.
create index ingestion_runs_source_success_idx
  on public.ingestion_runs (data_source_id, started_at desc)
  where status in ('succeeded', 'partial') and not dry_run;

-- WHO's FluNet page (https://www.who.int/toolkits/flunet) states that country-level data is
-- updated weekly and publishes a CSV download. It states NO licence; licence and
-- allow_public_export therefore stay unset until someone has read WHO's data policy and terms
-- of use and recorded the outcome (terms_verified_at).
update public.data_sources
   set source_type    = 'file_download',
       update_cadence = 'weekly (per WHO FluNet page; actual publication dates not yet observed)',
       notes = 'Country-level influenza laboratory surveillance. CSV endpoint: '
            || 'https://xmart-api-public.who.int/FLUMART/VIW_FNT?$format=csv . '
            || 'No sub-national (e.g. Karachi) resolution. Licence NOT verified: read WHO data policy / terms of use, '
            || 'then set license_name, license_url, terms_verified_at and allow_public_export. '
            || 'Source stays disabled until a first real ingestion has been reviewed.'
 where slug = 'who_flunet';
