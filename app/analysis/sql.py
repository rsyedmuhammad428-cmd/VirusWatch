"""SQL used by the analysis repository (bound parameters only; no string interpolation).

Kept separate so the exact statements can be executed against a real PostgreSQL in tests
(tests/db/test_analysis_sql.py) and checked against the repository's parameters
(tests/analysis/test_sql_contract.py).
"""

from __future__ import annotations

# All weekly observations for the indicators being analysed, one row per observation, ordered so
# that Python can group consecutive rows into series. `quality_flags` is returned as JSON text so
# every driver yields the same shape.
LOAD_POINTS = """
select o.id::text                          as observation_id,
       o.geography_id::text                as geography_id,
       g.country_code                      as country_code,
       o.disease                           as disease,
       o.pathogen                          as pathogen,
       o.indicator                         as indicator,
       o.surveillance_stream               as surveillance_stream,
       o.unit                              as unit,
       o.reporting_period_start            as period_start,
       o.reporting_period_end              as period_end,
       o.value_numeric                     as value,
       o.data_status                       as data_status,
       array_to_json(o.quality_flags)::text as quality_flags
  from public.observations o
  join public.geographies g on g.id = o.geography_id
 where o.indicator in (select jsonb_array_elements_text(cast(:indicators as jsonb)))
 order by o.geography_id, o.disease, o.pathogen nulls first, o.indicator,
          o.surveillance_stream nulls first, o.reporting_period_start
"""

# Idempotent: the natural key includes input_hash, so re-running on unchanged data inserts nothing.
INSERT_RESULTS = """
with incoming as (
  select * from jsonb_to_recordset(cast(:rows as jsonb)) as r(
    geography_id text, disease text, pathogen text, indicator text, surveillance_stream text,
    analysis_type text, analysis_period_start date, analysis_period_end date,
    baseline_period_start date, baseline_period_end date, latest_observation_end date,
    observed_value numeric, baseline_value numeric, effect_size numeric, score numeric,
    threshold numeric, status text, confidence_or_uncertainty jsonb, methodology_version text,
    parameters jsonb, missing_data_handling text, limitations text,
    input_observation_ids jsonb, input_hash text, explanation text
  )
),
inserted as (
  insert into public.analysis_results (
    geography_id, disease, pathogen, indicator, surveillance_stream, analysis_type,
    analysis_period_start, analysis_period_end, baseline_period_start, baseline_period_end,
    latest_observation_end, observed_value, baseline_value, effect_size, score, threshold,
    status, confidence_or_uncertainty, methodology_version, parameters, missing_data_handling,
    limitations, input_observation_ids, input_hash, explanation
  )
  select cast(r.geography_id as uuid), r.disease, r.pathogen, r.indicator, r.surveillance_stream,
         r.analysis_type, r.analysis_period_start, r.analysis_period_end,
         r.baseline_period_start, r.baseline_period_end, r.latest_observation_end,
         r.observed_value, r.baseline_value, r.effect_size, r.score, r.threshold, r.status,
         coalesce(r.confidence_or_uncertainty, '{}'::jsonb), r.methodology_version,
         coalesce(r.parameters, '{}'::jsonb), r.missing_data_handling, r.limitations,
         coalesce(array(select cast(x as uuid)
                          from jsonb_array_elements_text(r.input_observation_ids) as x),
                  '{}'::uuid[]),
         r.input_hash, r.explanation
    from incoming r
  on conflict on constraint analysis_natural_key do nothing
  returning 1
)
select (select count(*) from inserted) as inserted,
       (select count(*) from incoming) as submitted
"""
