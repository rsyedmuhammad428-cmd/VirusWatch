-- Phase 7: update data_sources.notes to reflect what was actually verified this phase. No row's
-- `enabled` flag changes here -- see README "Source status" and docs/PHASE7_REPORT.md for why:
-- `enabled` only gates *scheduled* automated runs (app.ingestion.runner), and both newly-built
-- connectors (cdc_fluview, pk_nih_weekly_bulletin) read from local, human-downloaded files rather
-- than a live, pollable endpoint, so unattended scheduling would just fail every time no one has
-- refreshed the files. A human can already run either one manually via
-- `python -m app.cli ingest <slug>` regardless of this flag.

update public.data_sources set notes =
  'US influenza surveillance (ILINet outpatient visits + WHO/NREVSS clinical and public-health lab '
  'positivity). Connector built and tested against real CDC-released files in Phase 7 '
  '(app.ingestion.connectors.cdc_fluview) with zero rejections. Reads three CSVs from a local '
  'directory, not a live API: CDC''s own FluView Interactive dashboard is backed by a POST-only, '
  'undocumented endpoint (confirmed to exist via the open-source cdcfluview R package, but its '
  'request-body schema for selecting each file was not independently verified from this sandbox), '
  'so this connector expects a human to use FluView''s own "Download Data" button and place the '
  'three files on disk -- see the connector module docstring.'
where slug = 'cdc_fluview';

update public.data_sources set notes =
  'Table 1 (province-wise suspected case counts) extracted from NIH''s weekly PDF bulletin. '
  'Connector built and tested in Phase 7 (app.ingestion.connectors.nih_bulletin, built on the '
  'already-tested app.datasets.nih_table parser) against the real Week 26, 2026 bulletin with zero '
  'rejections. No stable download API was found, so this connector reads human-downloaded PDFs '
  'from a local directory. KNOWN LIMITATION: this project''s observation schema has no '
  'sub-national geography field, so province-level counts are persisted as a single Pakistan-level '
  'total (summed only over provinces that reported, flagged when any province did not report); the '
  'full per-province breakdown is preserved in each observation''s metadata but is not yet '
  'independently queryable at that resolution.'
where slug = 'pk_nih_weekly_bulletin';

update public.data_sources set notes =
  'Wastewater SARS-CoV-2 concentrations are not comparable to clinical case counts and must never '
  'be combined with them. Phase 7 identified a real, public, no-auth Socrata dataset at '
  'data.cdc.gov (id j9g8-acpt, documented export URLs in docs/source_notes_cdc_nwss.md) but no '
  'connector was implemented: this sandbox has no network access to data.cdc.gov and no sample '
  'file was supplied, so the column schema was never confirmed and nothing was built against a '
  'guess. See docs/source_notes_cdc_nwss.md for exactly what would be needed to finish this.'
where slug = 'cdc_nwss';
