"""SQL for the surveillance and sources endpoints (bound parameters only).

Every filter is optional and written as `cast(:x as text) is null or ...` so one statement serves
all combinations. Tests run these verbatim on PostgreSQL (tests/db/test_surveillance_sql.py) and
check them against the routers' parameters (tests/test_sql_contract.py).

Public statements run as role `anon`, so row-level security decides what is visible.
"""

from __future__ import annotations

# Series filter shared by several statements. Columns: g = geographies, o = observations.
_SERIES_FILTER = """
   and (cast(:country as text) is null or g.country_code = :country)
   and (cast(:disease as text) is null or o.disease = :disease)
   and (cast(:indicator as text) is null or o.indicator = :indicator)
   and (cast(:stream as text) is null or o.surveillance_stream = :stream)
"""

_OBSERVATION_COLUMNS = """
       o.id::text                           as observation_id,
       g.country_code                       as country_code,
       g.country_name                       as country_name,
       g.admin_level                        as geographic_resolution,
       o.geography_id::text                 as geography_id,
       o.disease                            as disease,
       o.pathogen                           as pathogen,
       o.indicator                          as indicator,
       o.indicator_type                     as indicator_type,
       o.surveillance_stream                as surveillance_stream,
       o.reporting_period_start             as period_start,
       o.reporting_period_end               as period_end,
       o.value_numeric                      as value,
       o.unit                               as unit,
       o.numerator                          as numerator,
       o.denominator                        as denominator,
       o.data_status                        as data_status,
       array_to_json(o.quality_flags)::text as quality_flags,
       o.published_at                       as published_at,
       o.retrieved_at                       as retrieved_at,
       o.source_url                         as source_url,
       s.slug                               as source_slug,
       s.name                               as source_name,
       s.publisher                          as source_publisher,
       s.license_name                       as license_name,
       (s.terms_verified_at is not null)    as terms_verified
"""

_OBSERVATION_FROM = """
  from public.observations o
  join public.geographies g on g.id = o.geography_id
  join public.data_sources s on s.id = o.data_source_id
"""

GEOGRAPHIES = """
select g.id::text            as geography_id,
       g.country_code        as country_code,
       g.country_name        as country_name,
       g.admin_level         as geographic_resolution,
       g.region_name         as region_name,
       g.code_system         as code_system,
       count(o.id)           as observation_count,
       min(o.reporting_period_start) as first_period_start,
       max(o.reporting_period_end)   as latest_period_end
  from public.geographies g
  join public.observations o on o.geography_id = g.id
 group by g.id
 order by g.country_code, g.admin_level, g.region_name nulls first
"""

OBSERVATIONS = (
    "select"
    + _OBSERVATION_COLUMNS
    + ",\n       count(*) over () as total_count\n"
    + _OBSERVATION_FROM
    + " where true"
    + _SERIES_FILTER
    + """   and (cast(:date_from as date) is null or o.reporting_period_end >= :date_from)
   and (cast(:date_to as date) is null or o.reporting_period_start <= :date_to)
 order by o.reporting_period_end desc, o.id
 limit :limit offset :offset
"""
)

LATEST_OBSERVATIONS = (
    "select distinct on (o.geography_id, o.disease, o.pathogen, o.indicator, o.surveillance_stream)"
    + _OBSERVATION_COLUMNS
    + _OBSERVATION_FROM
    + " where true"
    + _SERIES_FILTER
    + """ order by o.geography_id, o.disease, o.pathogen, o.indicator, o.surveillance_stream,
          o.reporting_period_end desc
"""
)

# Chronological observations of series, for charts and comparisons.
SERIES_POINTS = (
    "select"
    + _OBSERVATION_COLUMNS
    + _OBSERVATION_FROM
    + " where true"
    + _SERIES_FILTER
    + """   and (cast(:date_from as date) is null or o.reporting_period_end >= :date_from)
   and (cast(:date_to as date) is null or o.reporting_period_start <= :date_to)
 order by g.country_code, o.surveillance_stream nulls first, o.reporting_period_start
 limit :limit
"""
)

# Same indicator and stream across several countries, chronological, for side-by-side display.
COMPARISON_POINTS = (
    "select"
    + _OBSERVATION_COLUMNS
    + _OBSERVATION_FROM
    + """ where o.indicator = :indicator
   and o.surveillance_stream is not distinct from cast(:stream as text)
   and g.country_code in (select jsonb_array_elements_text(cast(:countries as jsonb)))
   and (cast(:date_from as date) is null or o.reporting_period_end >= :date_from)
   and (cast(:date_to as date) is null or o.reporting_period_start <= :date_to)
 order by g.country_code, o.reporting_period_start
 limit :limit
"""
)

_ANALYSIS_COLUMNS = """
       a.id::text                   as analysis_id,
       g.country_code               as country_code,
       g.country_name               as country_name,
       g.admin_level                as geographic_resolution,
       a.geography_id::text         as geography_id,
       a.disease                    as disease,
       a.pathogen                   as pathogen,
       a.indicator                  as indicator,
       a.surveillance_stream        as surveillance_stream,
       a.analysis_type              as analysis_type,
       a.status                     as status,
       a.analysis_period_start      as period_start,
       a.analysis_period_end        as period_end,
       a.baseline_period_start      as baseline_start,
       a.baseline_period_end        as baseline_end,
       a.latest_observation_end     as latest_observation_end,
       a.observed_value             as observed_value,
       a.baseline_value             as baseline_value,
       a.effect_size                as effect_size,
       a.score                      as score,
       a.threshold                  as threshold,
       a.confidence_or_uncertainty::text as uncertainty,
       a.methodology_version        as methodology_version,
       a.missing_data_handling      as missing_data_handling,
       a.limitations                as limitations,
       cardinality(a.input_observation_ids) as input_count,
       a.explanation                as explanation,
       a.created_at                 as computed_at
"""

# Newest result per series and analysis type (RLS hides results under review or rejected).
LATEST_ANALYSES = (
    "select distinct on (a.geography_id, a.disease, a.pathogen, a.indicator,\n"  # noqa: S608 (constant fragments only)
    "                    a.surveillance_stream, a.analysis_type)"
    + _ANALYSIS_COLUMNS
    + """  from public.analysis_results a
  join public.geographies g on g.id = a.geography_id
 where (cast(:country as text) is null or g.country_code = :country)
   and (cast(:disease as text) is null or a.disease = :disease)
   and (cast(:indicator as text) is null or a.indicator = :indicator)
   and (cast(:stream as text) is null or a.surveillance_stream = :stream)
 order by a.geography_id, a.disease, a.pathogen, a.indicator, a.surveillance_stream,
          a.analysis_type, a.analysis_period_end desc, a.created_at desc
"""
)

SOURCES = """
select s.id::text          as source_id,
       s.slug              as slug,
       s.name              as name,
       s.publisher         as publisher,
       s.source_url        as source_url,
       s.source_type       as source_type,
       s.update_cadence    as update_cadence,
       s.enabled           as enabled,
       s.license_name      as license_name,
       s.license_url       as license_url,
       (s.terms_verified_at is not null) as terms_verified
  from public.data_sources s
 order by s.slug
"""

# Backend-only (service role): the freshness view exposes operational run status.
SOURCE_STATUS = """
select f.data_source_id::text          as source_id,
       f.slug                          as slug,
       f.name                          as name,
       f.enabled                       as enabled,
       f.update_cadence                as update_cadence,
       f.last_successful_run_at        as last_successful_run_at,
       f.last_run_status               as last_run_status,
       f.latest_reporting_period_end   as latest_reporting_period_end,
       f.latest_published_at           as latest_published_at,
       f.latest_retrieved_at           as latest_retrieved_at
  from public.source_freshness f
 order by f.slug
"""

# Admin only: includes error text.
SOURCE_RUNS = """
select r.id::text            as run_id,
       r.started_at          as started_at,
       r.completed_at        as completed_at,
       r.status              as status,
       r.triggered_by        as triggered_by,
       r.dry_run             as dry_run,
       r.records_downloaded  as records_downloaded,
       r.records_inserted    as records_inserted,
       r.records_updated     as records_updated,
       r.records_rejected    as records_rejected,
       r.checksum            as checksum,
       r.source_published_at as source_published_at,
       r.error_summary       as error_summary
  from public.ingestion_runs r
 where r.data_source_id = cast(:source_id as uuid)
 order by r.started_at desc
 limit :limit
"""
