# VirusWatch — data expansion & email scheduling: completion report

Date: 2026-11-01 · Scope: new datasets you supplied + Resend email scheduling · Builds on Phases 1-4 and `docs/DATASET_AUDIT_REPORT.md`, nothing in those is rebuilt.

## 0. What was and was not done

| Done | Not done |
|---|---|
| Live-browsed NIH, WHO, ECDC, OpenData Pakistan and Resend's own pages to check licences (§2, §6). | Called the live NIH/PHIS/IDMIS/CDC/ECDC APIs — this session has no credentials and, per `Prompt.txt`'s own sandbox notes, outbound HTTPS to arbitrary sites is not reachable from here. |
| Wrote pure validators and a provenance manifest for every file you attached, and ran them on the real files (§1, §3). | Wrote a loader that inserts validated rows into `public.observations`. The validators produce reports, not database writes — see §7. |
| Wrote and tested (with fakes, and on real PostgreSQL 16) the full email-scheduling pipeline: schedule math, duplicate-proof queueing, Resend send, Svix webhook status tracking (§4). | Sent a real email or received a real Resend webhook. No `RESEND_API_KEY` exists in this sandbox. |
| Ran the whole suite: **595 passed, 1 skipped** (the skip is the pre-existing Phase 1 FastAPI tests, unrunnable here as before). `ruff check` and `ruff format --check` clean. | Run the FastAPI app or its routers (same sandbox limitation as Phases 1-4). |
| Preserved every original file; computed, never typed, every checksum. | Deleted or edited any of your uploads. |

## 1. Datasets reviewed and their status

You asked me to pull from NIH, OpenData Pakistan, PHIS, IDMIS, WHO GHO, CDC and ECDC. This sandbox cannot reach those sites directly (no network from code, and WebFetch is restricted to URLs already in context), so "integrate" here means: validate everything you attached, and build the connector-independent pieces (provenance, validators, NIH table parser, email scheduler) so a future phase with real network/API access can plug in. The new files you attached:

| File | Data class | Licence | What it is | Problems found |
|---|---|---|---|---|
| `covid-19-cases-in-pakistan-2020.csv` | surveillance | **No licence stated** (verified on OpenData Pakistan's own page) | Daily province-level COVID-19 counts, Mar-Jun 2020 | Mixed date formats (`3/17/2020` vs `17-Mar-2020`) on the same day; one `"No 0"` text cell; 7 negative "tests in last 24h" values; cumulative counts decrease on 4 region-days; a repeated day with unchanged cumulative counts on 59 rows; `KPTD` and `Mobile Lab` are not real provinces. Only `Cumulative Test positive`, `Discharged`, `Expired` are mapped — the file has no data dictionary, so I did not guess at the other columns |
| `health_indicators_pak.csv` (WHO GHO export) | health_context | Unverified (WHO page names CC BY-NC-SA 3.0 IGO for *publications*, not specifically this data export) | 647 WHO indicators for Pakistan, 1950-2030 | **1,988 of 29,407 rows share a visible key with a different value** — the export dropped a disaggregating dimension (fuel type, age band, etc.); these rows are unattributable and are never loaded. No dengue, influenza or COVID-19 indicators exist in this file; it has malaria (12 indicators) and measles (3 indicators) |
| `national-dhs-quickstats.csv` | health_context | Not verified | DHS survey indicators for Pakistan, 1991/2006/2012/2017 surveys | Clean HXL-tagged CSV; fertility, vaccination, mortality — no disease surveillance indicators |
| `key-indicators-of-health-in-pakistan.csv` | health_context | Not stated | Immunisation/diarrhoea/mortality by province, 2013-14 vs 2018-19 | Small, clean; no publisher named on the file itself |
| `hiv-treatment-centres-in-pakistan-2020.csv` | health_context | Not stated | HIV ART centres and patient counts by facility | Clean; out of VirusWatch's five priority diseases |
| `pakistan.csv` / `pakistan_1.csv` (health facilities) | facility_reference | **No licence stated** on OpenData Pakistan; upstream is OpenStreetMap-derived (Gallup Pakistan / HDX) | 3,108 and 3,210 facility points | 470/490 rows have no coordinates, 236/237 have no name, the two files overlap on 3,099 `osm_id`s and `pakistan_1.csv` has 24 duplicate ids within itself. No disease data — reference only |
| `Public_Health_Bulletin_IDSRS_Weekly_Report-26-2026.pdf` + the "Merge dataset" 3-part, 813-page compilation | surveillance / **compilation** | Same as the NIH bulletins already audited | Weekly NIH national bulletins. The merged PDF is a **re-bundle of other documents, not a new source** — I parsed it to find which weeks exist, but treat each week's original bulletin as the source of record | Together with what you supplied earlier, **39 of 41 weeks of 2026 are now covered** (weeks 19 and 31 are absent from everything supplied); week 26 and week 41 each have an exact duplicate |
| `WINTER_61st-Issue-SAAL.pdf` | research/narrative | Same file as already audited (identical SHA-256) | Not a new dataset | — |

A full machine-readable manifest with SHA-256 checksums, coverage, geography and licence state for all 34 non-project files (old + new) is at `data/provenance/manifest.json`, generated by `scripts/build_manifest.py` from the files themselves — no field was typed by hand without a source.

## 2. Licences, checked against the publishers' own pages

| Source | Finding |
|---|---|
| WHO (GHO, FluNet) | WHO's publishing-policy page names **CC BY-NC-SA 3.0 IGO** for WHO *publications* and says commercial use needs permission. It does not state terms specifically for GHO/FluNet data exports. Treated as **unverified** — internal validation only, no public display, no redistribution, no commercial use, until WHO's data-specific terms are read and someone decides. |
| ECDC (Surveillance Atlas) | ECDC's copyright page states **CC BY 4.0** for web content and, "except where otherwise stated," for database data too — commercial reuse allowed with attribution and a standard credit line naming ECDC and the supplying authorities. Not yet used, since no ECDC file was supplied, but this is the licence to apply if a future phase pulls from the Atlas. |
| OpenData Pakistan (COVID-19 2020, health facilities) | Both dataset pages checked directly: **"No License Provided."** Internal use only; cannot be redistributed or shown publicly under VirusWatch's branding without separately contacting the publisher. |
| NIH Pakistan bulletins | No licence statement found on the documents themselves (as in the earlier audit). Unverified. |
| Resend | Not a data licence question — see §4 for its API/webhook contract, read from its own documentation. |

`app/datasets/catalog.py` encodes this as a small, explicit rule table (`permissions()`): `OPEN` → full use, `NONCOMMERCIAL` → display allowed, commercial/raw-redistribution blocked, `NONE_STATED`/`UNVERIFIED` → internal validation only, and `research`/`compilation` data classes are *never* redistributable regardless of stated licence (papers are cited, not hosted; compilations defer to their originals). A test (`tests/datasets/test_catalog_and_manifest.py::test_committed_manifest_loads_and_never_grants_unverified_permissions`) asserts every unverified/no-licence row in the real manifest is actually blocked from public display — this is a safety net, not a formality.

## 3. Validation code (`app/datasets/`)

Pure, side-effect-free modules, tested against **real excerpts** of your files (verbatim bytes from the actual CSVs/PDF text, in `tests/fixtures/datasets/`), not synthetic data:

- **`catalog.py` / `manifest.py`** — the `DatasetRecord`/`LicenceState`/`DataClass` model above, plus a JSON manifest with SHA-256 integrity checking (`verify_files`) and duplicate-content detection (`find_duplicate_content`).
- **`validators.py`**:
  - `validate_covid_2020` — parses both date formats, rejects/flags non-numeric and negative values, flags decreasing cumulative counts and non-province rows, reports date gaps. On the real file: 643 rows read, 0 rejected, flags raised on 13 non-province rows, 4 cumulative decreases, 59 unchanged-day repeats, 2 multi-day gaps.
  - `validate_gho` — flags ambiguous-key duplicate groups (1,917 groups across the real file) and reads only the `Numeric` column, never the display `Value` column (which has thousands separators and embedded confidence intervals like `"128.8 [121.2-136.7]"`).
  - `validate_facilities` — coordinate/name/duplicate-id checks; confirmed on both real files (results in §1).
- **`nih_table.py`** — extracts the NIH bulletin's province × disease "Table 1" from `pdftotext -layout` text. Treats `NR` as missing (never 0), flags when the printed "Total" column excludes non-reporting provinces (true for every week checked, since Punjab is consistently `NR`), and separately flags when a printed total doesn't match the sum of its own reported provinces (caught on the real week-26 table: `AD (Non-Cholera)` prints 85,516 but its reporting provinces sum to 85,066). Run across all 40 supplied bulletins: **0 rejections**, 39/41 weeks of 2026 found, duplicate weeks (26, 41) parse to identical values.
- **`scripts/build_manifest.py`** — built the committed manifest from the actual files (hashing, sizing); every licence/coverage/geography field traces to something I read on a source page or inside the file, and anything unverified is marked `UNVERIFIED`/`NONE_STATED`, never guessed as `OPEN`.

These validators produce reports (Python dataclasses); **no code here writes to `observations`**. Wiring a real loader needs: a canonical disease/indicator mapping for IDSR syndromes (e.g. is "AD (Non-Cholera)" in scope?), Sindh/Karachi geography rows (still absent, per the Phase 1-4 audit), and a decision on the COVID-19 2020 file's unlabelled columns — all flagged as open items below, not resolved by assumption.

## 4. Email scheduling (`app/notifications/`)

### Schedule and defaults
`schedule.py` implements calendar-based (not UTC-based) scheduling: a user's local day decides whether "today" is their delivery day, so Asia/Karachi's 9:00 AM never drifts across a date line. Defaults, as requested: **daily 9:00 AM, weekly Monday 9:00 AM, monthly 1st 9:00 AM, Asia/Karachi (UTC+05:00, no DST)**. Users can override time, weekday, day-of-month and timezone (`PUT /notifications/preferences`); `DeliverySchedule` validates the timezone against the real IANA database and rejects path-like injection attempts. Tested: 9 AM Karachi = 04:00 UTC in both winter and summer (no DST drift); DST spring-forward/fall-back handled explicitly for other timezones a user might pick.

### Duplicate prevention
This is enforced by the database, not application memory: `notification_history` keeps its pre-existing `UNIQUE (user_id, deduplication_key)` constraint. The deduplication key is deterministic — `digest:<user>:weekly:2026-W45` or `alert:<analysis_result_id>` — so the same period or the same anomaly can only ever produce one row, confirmed on real PostgreSQL: a second `ENQUEUE` for the same key returns no row, and two overlapping scheduler runs can't both send (`tests/db/test_notifications_sql.py::test_enqueue_is_the_duplicate_guard`, `test_claim_due_takes_each_row_once...`).

### Immediate high-priority alerts
`planner.alert_decision` is the single gate, and it requires **all** of: the user opted into alerts; the result's status meets their minimum severity (only `elevated`/`possible_anomaly` — the engine's own vocabulary, see Phase 4); the result's `review_status` is `approved` **by default** — an unreviewed (`auto`) result is not alerted unless the operator explicitly sets `ALERT_ACCEPT_UNREVIEWED=true`; the latest data is not stale (28-day rule, reused from the analysis engine); the observation isn't flagged `small_denominator`; and it matches the user's selected diseases/geography if they set any. Every gate has its own test.

### Status tracking and Resend webhooks
`status.py` is a small state machine: status only moves forward (`pending → sending → sent → delivered`) and a negative outcome (bounced/complained/failed/suppressed) is never overwritten by a later positive event — verified against Resend/Svix's own documented webhook contract (`svix-id`/`svix-timestamp`/`svix-signature` headers, HMAC-SHA256 over `id.timestamp.body`, 5-minute tolerance, at-least-once delivery). `webhook.py` verifies signatures and is unit-tested with locally generated signatures (forged, stale, wrong-secret, replayed-id cases all rejected) — **it has never been exercised against a real Resend account**, since no API key exists here. Webhook event ids are stored idempotently (`email_events.webhook_event_id` unique), so a Resend retry is a no-op.

### Delivery semantics
A row is claimed (`pending → sending`, via `SELECT ... FOR UPDATE SKIP LOCKED`) before Resend is called. If the process dies between a successful send and recording it, the row stays `sending` forever rather than risking a duplicate email — `stuck_sending` surfaces these for an operator; nothing auto-retries them. Transient provider errors (timeouts, 429, 5xx) retry with backoff up to `max_attempts`; permanent errors (4xx other than 429) fail immediately. One unhandled exception in one user's send is caught and recorded without stopping the batch, and its message is never used verbatim in the stored error (tested: a `RuntimeError("secret 123")` never leaks "secret" into `error_summary`).

### Content
`digest.py` renders plain, HTML-escaped text. It never asserts "outbreak confirmed," only the analysis engine's own status wording ("Possible statistical anomaly," etc.), and always ends with a disclaimer that a flag is not medical advice — reusing the same caution the Phase 4 surveillance API already enforces.

### New migration
`supabase/migrations/20261031000010_email_scheduling.sql` adds `delivery_time`/`weekly_day`/`monthly_day` to `notification_preferences` (defaults 09:00/Monday/1st, Asia/Karachi), renames the enum value `queued`→`pending` to match the product vocabulary, and adds `scheduled_for`/`sent_at`/`last_event_*` plus two partial indexes (`due`, `stuck_sending`) to `notification_history`. Applied and tested on real PostgreSQL 16 — RLS was re-checked too: clients still cannot read `email_events` or `notification_history`, or write `notification_preferences` directly (`test_clients_cannot_read_or_write_delivery_tables`).

### Wiring
New API endpoints: `PUT /notifications/preferences` (schedule + consent; still requires a verified email and an explicit consent flag, as the existing `CHECK` constraint already demanded), `POST /notifications/unsubscribe` (token-based, always 204 to avoid token enumeration), `POST /webhooks/resend`. New CLI command `python -m app.cli notify` runs one dispatch cycle (intended to run on a schedule itself, e.g. cron or a Supabase Edge Function — no such scheduler is configured in this sandbox). All of this is **written and tested, never executed end-to-end against FastAPI** (same limitation as the rest of the API layer, since FastAPI isn't installable here).

## 5. Code changes (file list)

New: `app/datasets/{__init__,catalog,manifest,validators,nih_table}.py`, `app/notifications/{__init__,schedule,planner,ports,dispatcher,status,webhook,sender,digest,sql,sql_repository}.py`, `app/api/v1/notifications.py`, `supabase/migrations/20261031000010_email_scheduling.sql`, `scripts/build_manifest.py`, `data/provenance/manifest.json`, `tests/datasets/*`, `tests/notifications/*`, `tests/db/test_notifications_sql.py`, `tests/fixtures/datasets/*`.
Changed: `app/api/v1/__init__.py` (new router), `app/config.py` (`alert_accept_unreviewed`, `notification_batch_size`), `app/cli.py` (`notify` command), `tests/test_sql_contract.py` (added `app.notifications` to the static SQL-contract scan), `README.md` (status/roadmap).
Nothing in Phases 1-4 was rewritten; `app/analysis`, `app/ingestion`, `app/surveillance` are untouched.

## 6. Test results

- Whole suite (pure + real PostgreSQL 16, `NO_PGVECTOR=1`): **595 passed, 1 skipped**. The skip is the same pre-existing Phase-1 `fastapi` import gap noted since Phase 1 — not new.
- New tests specifically: 28 dataset tests (`tests/datasets/`), 114 scheduler tests with fakes (`tests/notifications/`), 23 real-PostgreSQL notification SQL tests (`tests/db/test_notifications_sql.py`).
- `ruff check .` and `ruff format --check .`: clean.
- Not run: FastAPI app/routers (sandbox has no `fastapi`), a real Resend send, a real NIH/WHO/CDC/ECDC API call.

## 7. Remaining limitations and decisions needed from you

1. **No loader yet.** Validated dataset reports do not become `observations` rows. That needs: a canonical mapping from IDSR syndromes (AD, ILI, SARI, ...) and the 2020 COVID-19 file's columns to VirusWatch indicators, and new Sindh/Karachi/province geography rows (flagged in the Phase 1-4 audit, still open).
2. **Licences remain the blocker for public display** of FluNet, GHO, and both OpenData Pakistan files — all are `UNVERIFIED`/`NONE_STATED` and therefore internal-only under the rule in §2 until someone reads WHO's data-specific terms or contacts OpenData Pakistan's publishers.
3. **Live connectors to NIH/PHIS/IDMIS/CDC/ECDC/OpenData Pakistan are not built** — this sandbox cannot reach those sites from code, so only the files you attached were processed. A future phase with real network access would reuse the existing `app/ingestion` connector framework (Phase 3) and the validators built here.
4. **COVID-19 2020 file's unlabelled columns** (`Cumulative`, `New (last 24 hrs)`, `Tests performed...`) are deliberately left unmapped — the source gives no data dictionary and the values are inconsistent (cumulative test counts in some rows, something else in others). Mapping them needs either the publisher's documentation or your confirmation of what they mean.
5. **Email scheduling and Resend integration are untested against a live account.** Before using this in production: set `RESEND_API_KEY`/`EMAIL_FROM`/`RESEND_WEBHOOK_SECRET`, send a real test message, and register the webhook URL with Resend.
6. **`ALERT_ACCEPT_UNREVIEWED` defaults to false** — immediate alerts require a human-approved analysis result. Leave this off unless you have a review workflow in place; turning it on sends alerts on the engine's own unreviewed output.
