# CDC NWSS (wastewater surveillance): findings for the Phase 7 connector decision

Status: **source identified and confirmed real/public; no connector implemented.**

## What was confirmed

CDC's National Wastewater Surveillance System publishes its SARS-CoV-2 dataset as a public,
no-authentication Socrata dataset on data.cdc.gov:

- Catalog page: https://catalog.data.gov/dataset/cdc-wastewater-data-for-sars-cov-2
- Dataset identifier: `j9g8-acpt`
- Documented export URLs (from the catalog page's own distribution metadata):
  - CSV: `https://data.cdc.gov/api/v3/views/j9g8-acpt/export.csv?accessType=DOWNLOAD`
  - JSON: `https://data.cdc.gov/api/v3/views/j9g8-acpt/query.json?accessType=DOWNLOAD`
  - XML: `https://data.cdc.gov/api/v3/views/j9g8-acpt/query.xml?accessType=DOWNLOAD`
- Access level: public. License: U.S. government work. Update cadence: weekly.
- Publisher/contact: NWSS / CDC.

This is a real, documented, publicly accessible download -- not an invented endpoint -- found via
the data.gov catalog record for the dataset (not a reverse-engineered internal API).

## What was not confirmed, and why no connector exists yet

The catalog page does not include a data dictionary or column list, and this sandbox has no
network egress to `data.cdc.gov` (outbound HTTP from the shell is allow-listed to package
registries and GitHub only; the WebFetch tool itself also rejected `data.cdc.gov` as outside its
allowlist when a sample row was requested). No sample NWSS file was supplied by the project owner
either. Without having seen a single real row, the column names, units, site/jurisdiction
identifiers, and how "detection level" or "percentile" values are computed are all unknown here.

Writing a parser against guessed column names would be exactly the kind of fabrication the project
rules prohibit -- "Do not invent API endpoints" extends in spirit to not inventing a schema for a
real endpoint either. Per section 5 of the master spec ("Document unsupported sources as
unavailable until verified"), `cdc_nwss` stays seeded in `data_sources` with `enabled = false` and
unregistered in `app/ingestion/connectors/__init__.py`.

## What it would take to finish this

1. Download one real CSV from the export URL above (requires network access this sandbox does not
   have), or have the project owner attach one.
2. Confirm the column names against the CDC NWSS data-dictionary page
   (https://www.cdc.gov/nwss/index.html, not reviewed here) or by inspecting the file's own
   header.
3. Write `app/ingestion/connectors/cdc_nwss_parser.py` and `cdc_nwss.py` following the same
   pattern as `cdc_fluview_parser.py`/`cdc_fluview.py`: site/jurisdiction-level reporting,
   `indicator_type = "wastewater"`, and an explicit note (required by the spec) that wastewater
   measurements are never interchangeable with reported clinical case counts.
4. Register it in the connector registry and flip `data_sources.enabled` for `cdc_nwss`.

Nothing here claims this was done. It was not.
