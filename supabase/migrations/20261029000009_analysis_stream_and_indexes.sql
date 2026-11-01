-- 0009: make analysis results stream-aware and speed up the surveillance read paths.
--
-- FluNet sentinel and non-sentinel data are separate series (see 0008). An analysis of one must
-- never be confused with, or overwritten by, an analysis of the other, so analysis_results gets
-- the same surveillance_stream dimension and it joins the natural key.

alter table public.analysis_results
  add column surveillance_stream text
    check (surveillance_stream is null
           or surveillance_stream in ('sentinel', 'nonsentinel', 'undefined'));

comment on column public.analysis_results.surveillance_stream is
  'Sampling stream of the analysed series (see observations.surveillance_stream). NULL when the source has no such concept.';

alter table public.analysis_results drop constraint analysis_natural_key;
alter table public.analysis_results
  add constraint analysis_natural_key unique nulls not distinct
    (geography_id, disease, pathogen, indicator, surveillance_stream, analysis_type,
     methodology_version, analysis_period_end, input_hash);

-- "Latest result per series" is the hot query for /surveillance/latest and /alerts.
drop index public.analysis_results_series_idx;
create index analysis_results_series_idx
  on public.analysis_results
  (geography_id, disease, indicator, surveillance_stream, analysis_type, analysis_period_end desc, created_at desc);
