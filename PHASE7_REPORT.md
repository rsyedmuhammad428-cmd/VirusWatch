# Phase 7 — NIH + CDC additional data sources — completion report

Date: 2026-11-06 · Scope: Prompt.txt's Phase 7 line ("NIH, CDC additional data sources — test
geographic and indicator comparability") · Status: **two new connectors built and tested against
real supplied data; one real source identified and honestly deferred; comparability proven with
dedicated tests; a new batch of attached datasets validated and cataloged (not ingested)**

This report follows the same 8-point structure as every prior phase report.

## 0. What was and was not done

| Done this phase | Not done this phase |
|---|---|
| **CDC FluView connector** (`app/ingestion/connectors/cdc_fluview.py` + `cdc_fluview_parser.py`): parses the real ILINet.csv, ICL_NREVSS_Clinical_Labs.csv, ICL_NREVSS_Public_Health_Labs.csv you supplied; registered and ingestible via `python -m app.cli ingest cdc_fluview` | Running it against a live download — CDC's actual data-serving endpoint is POST-only with an unconfirmed request schema (see §2); this connector reads local files, like NIH's |
| New `app/ingestion/mmwr.py`: correct MMWR (CDC epi-week) date arithmetic, verified against a known reference date, used instead of WHO's ISO-week logic | — |
| **NIH bulletin connector** (`app/ingestion/connectors/nih_bulletin.py`): wires the already-tested `app.datasets.nih_table` parser into the same runner/persistence pipeline as every other connector (dedup by checksum, rejection reporting) | Sub-national (province) geography — the schema has no field for it yet; see the connector's own module docstring for how this is handled instead of silently dropped |
| **CDC NWSS (wastewater)**: the real, public, documented Socrata endpoint was identified and confirmed (`docs/source_notes_cdc_nwss.md`) | No connector — no sample row or column list could be obtained from this sandbox (no network egress to data.cdc.gov, none supplied), and guessing a schema for a real endpoint would be the same kind of fabrication as inventing one |
| Geographic/indicator comparability: a dedicated test file proving the *new* indicators interact correctly with the existing comparison guard | — |
| 8 new datasets you attached this round validated (licence, data class, coverage, quality issues) and added to `data/provenance/manifest.json` | **Not ingested.** Validation only — see `docs/DATASET_AUDIT_REPORT.md` §9. One of the eight (`kp_idsr_district_week_compilation`) needs a decision from you before it can be used at all |

## 1. Files created or modified

**New:**
- `app/ingestion/mmwr.py` — MMWR week arithmetic.
- `app/ingestion/connectors/cdc_fluview_parser.py`, `cdc_fluview.py` — pure parser + connector.
- `app/ingestion/connectors/nih_bulletin.py` — connector wrapping `app.datasets.nih_table`.
- `docs/source_notes_cdc_fluview.md` (from the earlier audit pass, now acted on), `docs/source_notes_cdc_nwss.md` (new).
- `tests/ingestion/test_mmwr.py`, `test_cdc_fluview_parser.py`, `test_cdc_fluview_connector.py`, `cdc_fluview_helpers.py`, `test_nih_bulletin_connector.py`.
- `tests/surveillance/test_cross_source_comparability.py`.
- `scripts/build_manifest_phase7_addendum.py` — reproducible addition of the 8 new dataset records.
- `data/cdc_fluview/` (the 3 real CSVs you supplied, staged where the connector reads them) and `data/nih_bulletins/` (the real bulletin PDF).

**Modified:**
- `app/ingestion/connectors/__init__.py` — registered `cdc_fluview` and `pk_nih_weekly_bulletin`; left `cdc_nwss` unregistered with a comment explaining why.
- `data/provenance/manifest.json` — 8 new records appended (34 → 42); no existing record changed.
- `docs/DATASET_AUDIT_REPORT.md` — new §9 addendum covering the new batch.

## 2. Features implemented, and the honesty calls behind them

**CDC FluView.** The parser handles everything found when actually reading your real files: a
one-line preamble before the header, `X` as a missing-value marker (and as CDC's placeholder for
the REGION column on national rows — a labelling convention, not a missing value, and the two are
not confused), MMWR weeks, and three genuinely different indicators that are never merged:
`ili_pct_weighted` (outpatient syndromic surveillance, "sentinel" stream), and two separate
laboratory-positivity numbers — `influenza_positivity_clinical` and
`influenza_positivity_public_health` — because NREVSS's clinical and public-health labs are
different reporting networks with different selection biases, kept in separate CSVs by CDC itself
for that reason. Public-health labs' subtype counts (A/H1N1, A/H3, A/H5, H3N2v, B, BVic, BYam) are
summed to one positivity number rather than invented as seven more unverified indicator slugs; the
individual counts are preserved losslessly in each observation's `metadata`.

**Why `fetch()` reads local files instead of calling a live endpoint.** CDC's FluView Interactive
dashboard is backed by a real endpoint (`gis.cdc.gov/grasp/flu2/PostPhase02DataDownload` —
confirmed via the source of the actively-maintained open-source `cdcfluview` R package, not
invented). It is POST-only with a JSON body, and this codebase's shared `HttpFetcher` port
(used by WHO FluNet too) is GET-only; extending it to support one reverse-engineered POST body
whose exact parameter values for selecting ILINet vs. the two NREVSS files were not independently
confirmed from this sandbox would mean shipping a guess dressed up as a real integration. Per the
master spec's "do not assume a stable API exists," `fetch()` instead reads the three CSVs from a
local directory (a human uses CDC's own "Download Data" button and saves them there) — the same
pattern NIH's connector already uses, now also tested the same way.

**NIH bulletin connector.** Wraps the already-tested `app.datasets.nih_table` parser (which
extracts Table 1 — province-wise suspected cases — from `pdftotext -layout` output) into the
runner pipeline. The real geographic-fidelity limitation is stated plainly, not hidden: the
schema has no sub-national geography field, so each province's figure is preserved losslessly in
`metadata`, and the persisted country-level total carries a `total_excludes_non_reporting` quality
flag whenever any province didn't report, rather than silently presenting a partial sum as a full
national count. Tested against your real Week 26, 2026 bulletin PDF end to end (fetch → parse →
the flag is actually present on the resulting observation).

**CDC NWSS (wastewater).** A real, public, no-authentication Socrata dataset was located and
confirmed (`data.cdc.gov`, dataset id `j9g8-acpt`, documented CSV/JSON/XML export URLs — see
`docs/source_notes_cdc_nwss.md` for exactly how this was confirmed). No connector was written:
there is no column schema available to verify a parser against (no network egress to data.cdc.gov
from this sandbox, and no sample file was supplied), and writing one from a guessed schema for a
real endpoint is the same category of error as inventing the endpoint itself. `cdc_nwss` stays
seeded in `data_sources` with `enabled = false`.

**Comparability tests.** `tests/surveillance/test_cross_source_comparability.py` proves, against
the real parser code (not synthetic stand-ins), that: none of CDC's three new indicator slugs can
enter the cross-country comparison endpoint (`indicator_not_comparable`); a `clinical_surveillance`
row can't be pooled with a `laboratory_positivity` row even if someone tried
(`indicator_type_not_comparable`); the two NREVSS lab types produce genuinely distinct indicator
slugs and `source_record_id`s for the same week, so no query could ever sum them; NIH's
`suspected_cases_reported` (a raw count, no denominator) is likewise refused by the comparison
endpoint; and a province that didn't report (`NR`) correctly flags the country total as partial
rather than silently looking complete.

## 3. Commands to run

```bash
# CDC FluView: download the 3 CSVs from CDC's "Download Data" page, save to data/cdc_fluview/,
# then:
python -m app.cli ingest cdc_fluview

# NIH bulletin: save PDF(s) to data/nih_bulletins/, then:
python -m app.cli ingest pk_nih_weekly_bulletin

# Tests:
export TEST_PG_ADMIN_CONN="host=/path/to/socket/dir port=5432 user=postgres dbname=postgres"
NO_PGVECTOR=1 python -m pytest -q
ruff check . && ruff format --check .

# Regenerating the manifest with this phase's new entries (already run; included in the zip):
python3 scripts/build_manifest_phase7_addendum.py --uploads DIR --extracted DIR
```

## 4. Environment variables needed

None new. CDC FluView and NIH bulletin both read local files; no new credentials.

## 5. Database migrations applied or pending

None. `cdc_fluview` and `pk_nih_weekly_bulletin` were already seeded as `data_sources` rows
(migration `20261029000004`, written before this phase, `enabled = false`). This phase's CLI
`sources enable` command is what flips a source live once an operator has placed real files on
disk — no schema change was needed.

## 6. Tests run and their actual outcomes

```
NO_PGVECTOR=1 TEST_PG_ADMIN_CONN=... python -m pytest -q
  --ignore=tests/test_auth.py --ignore=tests/test_health_and_errors.py --ignore=tests/test_config.py
→ 845 passed, 4 skipped
ruff check .          → All checks passed!
ruff format --check . → 174 files already formatted
```

Of the 845: 70 are in the six files this phase added or most directly exercises (`test_mmwr.py`,
`test_cdc_fluview_parser.py`, `test_cdc_fluview_connector.py`, `test_nih_bulletin_connector.py`,
`test_cross_source_comparability.py`: 9 tests, `test_catalog_and_manifest.py` — this last one
already existed but now also exercises the 8 newly added manifest records, including the
regression test that UNVERIFIED/NONE_STATED entries never get `public_display` permission).
`test_cross_source_comparability.py` proves: none of CDC's three new indicator slugs can enter the
cross-country comparison endpoint; CDC's two NREVSS lab types produce genuinely distinct indicator
slugs and per-row metadata tags for the same week, so no query could pool them even though both are
`laboratory_positivity`/percent; NIH's `suspected_cases_reported` (a raw count, no denominator) is
likewise excluded from the comparable-indicator-type set; and both connectors' country scoping is
proven against real parsed data (CDC FluView emits `US` only — including when explicitly filtered
to `PK`, which correctly returns zero rows rather than relabelling anything; NIH emits `PK` only).
The NR-partial-total flag (`total_excludes_non_reporting`) has its own dedicated coverage in
`tests/datasets/test_nih_table.py` and `tests/ingestion/test_nih_bulletin_connector.py`, not in the
comparability file. The rest is the unchanged Phase 1-6 suite re-run to confirm no regression. The
3 excluded files/modules are the same pre-existing `fastapi`/`structlog`/`pydantic-settings` gap as
every previous phase — FastAPI is still not installable in this sandbox, so the CLI path (tested
above) is this phase's only executed path; the HTTP route layer for these sources is unchanged
from earlier phases.

**CDC FluView's `fetch()` and NIH's `fetch()` were tested against real files, never against a live
network.** `parse()` for both was tested against the real CSV/PDF content you supplied. The
*parsing* is proven against real data; the *live download* step for CDC FluView has never run
(see §2 for exactly why), and NIH's live download step doesn't exist by design (no stable API).

## 7. Manual steps requiring your account or credentials

- Nothing for CDC FluView or NIH bulletin ingestion itself — both work from files a human
  downloads and places on disk, no account needed.
- A Project Tycho account + API key, if you want to pursue `tycho_api_reference` in a future phase
  (documented API, registration required, not attempted here).
- Confirmation of the `kp_idsr_district_week_compilation` dataset's actual authorship/citation (see
  `docs/DATASET_AUDIT_REPORT.md` §9) before any further work touches it.

## 8. Known limitations and next phase

- **CDC FluView's live download is unverified, by design, not oversight.** See §2. If CDC
  publishes (or you can confirm) a simple GET-based bulk-download URL for these three files,
  `fetch()` is the only method that needs to change.
- **CDC NWSS has no connector.** A real, documented, public data source was found, but writing a
  parser against an unseen schema was judged worse than not writing one. Get one real sample row
  (either network access to `data.cdc.gov` or an attached file) and this is a same-day addition
  following the CDC FluView pattern.
- **NIH province geography is lossy by schema, not by this connector.** Every province figure is
  preserved in `metadata`, but it isn't queryable as its own geography row yet — that needs a
  cross-cutting schema change (a geographies table, RLS policies, surveillance API changes),
  correctly out of scope for a connector-only phase.
- **The 8 newly validated datasets are not ingested.** Building indicator definitions and
  ingestion for dengue (Kaggle Pakistan, OpenDengue) and malaria (Malaria Atlas Project estimates)
  data is its own phase of work, not represented here as done. One of the eight needs your
  decision before it can be used at all (`kp_idsr_district_week_compilation` — see
  `docs/DATASET_AUDIT_REPORT.md` §9).
- Per the master spec's phase order, Phase 8 (frontend integration docs) is next.
