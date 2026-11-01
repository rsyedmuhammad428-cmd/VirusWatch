# VirusWatch backend

AI-assisted infectious-disease **surveillance information** platform (Pakistan first, comparable with the US).
It reports surveillance indicators and flags statistical anomalies. It does **not** diagnose people, measure
airborne viruses, or confirm outbreaks because a number went up.

> **Status: Phases 1–4 of 9.** Skeleton, schema, RLS, auth, WHO FluNet ingestion, the statistical analysis engine and the
> surveillance API exist. **Everything has been tested on synthetic fixtures only; no real WHO download has been run, the
> database contains zero real observations, and the HTTP layer has never been started** (see [Status](#status)). See [Status](#status) for exactly what is and is not verified.

## File structure

```
viruswatch/
├── README.md                  this file
├── requirements.txt           runtime packages
├── requirements-dev.txt       runtime + pytest + ruff
├── pyproject.toml             project metadata, same dependencies, pytest/ruff config
├── .env.example               every environment variable, placeholders only
├── .gitignore  .dockerignore
├── Dockerfile                 non-root image running the API via the app factory
├── .github/workflows/ci.yml   CI: ruff + pytest, and DB tests on Postgres+pgvector
│
├── app/                       the FastAPI backend
│   ├── main.py                create_app(): middleware order, CORS, routers
│   ├── config.py              Settings (env vars); rejects wildcard CORS, validates production
│   ├── core/
│   │   ├── auth.py            Supabase JWT verification (ES256/RS256 via JWKS, HS256 legacy)
│   │   ├── errors.py          AppError + uniform {"error": {...}} responses
│   │   ├── middleware.py      request id + access log; request-size limit
│   │   └── logging_config.py  structlog JSON logging with secret redaction
│   ├── cli.py                 `python -m app.cli ingest|sources` (exit codes 0 ok, 1 failed, 2 partial, 3 busy)
│   ├── db/session.py          async engine; anon / authenticated / service sessions (RLS-aware)
│   ├── analysis/              Phase 4: deterministic statistics (no DB, no AI)
│   │   ├── stats.py           median, MAD, robust scale, Theil-Sen, Mann-Kendall
│   │   ├── engine.py          freshness, rolling mean, trend, baselines, anomaly screening
│   │   ├── models.py          config + thresholds, Point, AnalysisResult, METHODOLOGY_VERSION
│   │   ├── loading.py service.py   rows -> series; run all series and store results
│   │   └── sql.py sql_repository.py serialization.py ports.py errors.py
│   ├── surveillance/          Phase 4: read side of the API
│   │   ├── queries.py         SQL for the endpoints (bound parameters only)
│   │   ├── service.py         response builders + safety rules (comparisons, staleness, alerts)
│   │   └── schemas.py         response models (numbers serialise as JSON numbers)
│   ├── ingestion/             Phase 3: WHO FluNet ingestion (ports & adapters, no framework needed to test)
│   │   ├── connectors/        base.py, flunet.py (fetch + checksum), flunet_parser.py (CSV -> observations)
│   │   ├── runner.py          run bookkeeping, status rules, change detection, reject-fraction gate
│   │   ├── ports.py           Repository and HttpFetcher protocols
│   │   ├── sql.py             every SQL statement (bound parameters only)
│   │   ├── sql_repository.py  SQLAlchemy implementation of the repository
│   │   ├── httpx_fetcher.py   httpx implementation of the fetcher (retries, size cap)
│   │   └── models.py errors.py countries.py http.py serialization.py
│   └── api/
│       ├── deps.py            auth + DB dependencies (require_user, require_admin, ...)
│       ├── health.py          GET /health, GET /ready
│       └── v1/                me.py, surveillance.py, sources.py, common.py
│
├── supabase/migrations/       database schema (apply with the Supabase CLI), in order:
│   ├── ..0001_extensions_and_helpers.sql   pgvector, updated_at trigger
│   ├── ..0002_core_surveillance.sql        sources, runs, geographies, observations, revisions, analyses
│   ├── ..0003_rag_and_ops.sql              documents, chunks (vector 768), feedback, evaluations, job runs
│   ├── ..0004_users_and_notifications.sql  roles, profiles, notification prefs/history, email events
│   ├── ..0005_rls_and_grants.sql           RLS on every table + least-privilege grants
│   ├── ..0006_functions_and_views.sql      match_document_chunks(), source_freshness view
│   ├── ..0007_seed_reference_data.sql      4 disabled sources + PK/US country rows (no observations)
│   ├── ..0008_flunet_ingestion_support.sql surveillance_stream in the natural key, indexes, FluNet metadata
│   └── ..0009_analysis_stream_and_indexes.sql  analysis results become stream-aware; read-path index
│
├── scripts/test_db.sh         apply migrations to a disposable Postgres and run the SQL checks
└── tests/
    ├── conftest.py  test_config.py  test_auth.py  test_health_and_errors.py
    ├── analysis/              statistics (hand-checked), engine rules, loading, service, CLI
    ├── surveillance/          response builders: geography notes, staleness, alerts, comparisons
    ├── ingestion/             parser, connector, runner, CLI, serialization
    ├── test_sql_contract.py   repositories pass exactly the parameters their SQL declares
    ├── test_router_sql_contract.py   same check for the API routers
    ├── fixtures/flunet/       SYNTHETIC fixture (not real WHO data), its README, WHO field dictionary
    └── db/ bootstrap.sql  checks.sql (RLS/constraint assertions)  no_pgvector.sed
        psql_harness.py  helpers.py  conftest.py   (run real SQL through psql, optionally as role anon)
        test_ingestion_sql.py  test_analysis_sql.py  test_surveillance_sql.py   (on a real Postgres)
docs/                          methodology_analysis.md  api_surveillance.md  source_notes_cdc_fluview.md  samples/
```

## Required software and packages

System: **Python 3.12+**, **PostgreSQL client (`psql`)** for the DB tests, the **Supabase CLI** to push migrations,
and optionally Docker. Python packages (`requirements.txt`):

| Package | Used for |
|---|---|
| fastapi, uvicorn[standard] | web framework and server |
| pydantic, pydantic-settings | request/response models, env-var settings |
| sqlalchemy[asyncio], asyncpg | async PostgreSQL access (Core, no ORM models) |
| PyJWT[crypto] | verifying Supabase access tokens (includes `cryptography`) |
| structlog | structured JSON logs |
| httpx | HTTP client for upcoming source connectors, Gemini, Resend |
| pandas | data processing for upcoming ingestion/analysis |

Dev only (`requirements-dev.txt`): pytest, ruff. Later phases will add PDF/RAG packages (e.g. pdfplumber/PyMuPDF).

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # or: pip install -e ".[dev]"
cp .env.example .env            # fill in placeholders; never commit .env
pytest -q
uvicorn app.main:create_app --factory --reload
# http://127.0.0.1:8000/health   /ready   /docs   /openapi.json
```

`/ready` returns 503 until `SUPABASE_DB_URL` is set **and** the migrations are applied. That is intentional.

## Database

Migrations live in `supabase/migrations/` (timestamped, Supabase CLI format). Alembic is deliberately not used:
Supabase migrations are the only schema source, so there is no second history to drift.

```bash
supabase init                      # once; creates supabase/config.toml, leaves migrations alone
supabase link --project-ref <ref>
supabase db push                   # hosted project
# or locally: supabase start && supabase db reset
```

Grant the first admin (SQL editor; admin status is never read from a token or request body):

```sql
insert into public.user_roles (user_id, role) values ('<auth.users id>', 'admin');
```

### Database tests (no Supabase needed)

```bash
# Any disposable Postgres 15+ with pgvector; the database must be empty.
scripts/test_db.sh "host=localhost port=5432 user=postgres password=postgres dbname=viruswatch_test"
# Postgres without pgvector: NO_PGVECTOR=1 scripts/test_db.sh "..."   (see tests/db/no_pgvector.sed)
```

The ingestion SQL tests need only the `psql` client and a superuser connection to a disposable server:

```bash
TEST_PG_ADMIN_CONN="host=localhost port=5432 user=postgres password=postgres" pytest tests/db -q
# add NO_PGVECTOR=1 for a server without pgvector; the tests skip when the variable is unset
```

CI runs both against `pgvector/pgvector:pg16` (`.github/workflows/ci.yml`).

## WHO FluNet ingestion (Phase 3)

```bash
python -m app.cli sources                                   # what is configured, enabled, last run
python -m app.cli ingest --source who_flunet --dry-run      # real download + real upsert, rolled back
python -m app.cli ingest --source who_flunet --countries PAK,USA --since-year 2023
python -m app.cli ingest --source who_flunet --backfill     # all years (large download)
```

Flags: `--force` (ignore unchanged-data check), `--scheduled` (marks the trigger), `--raw-dir DIR`
(keep the downloaded file), `--json` (machine-readable summary). Defaults come from the
`INGESTION_*` variables in `.env.example`; the default window is the previous and current ISO year.

**What is stored.** Per country, ISO week (Monday-Sunday) and surveillance stream (sentinel,
non-sentinel, not-defined, kept as separate series): `specimens_processed`, `influenza_positive_count`
(total, A, B and subtypes when reported) and `influenza_positivity`.

**Methodology rules (tested).**
- A blank source cell is *missing*, never zero. A source that later blanks a value makes it `missing`.
- Positivity = positives / (positives + negatives) x 100, 4 decimals, flagged `derived_by_viruswatch`,
  with numerator and denominator kept. Zero tests gives *missing*, not 0%. Small denominators (<10) and
  a denominator that differs from specimens processed are flagged.
- Rows are validated one by one; rejected rows are counted by reason. If more than
  `INGESTION_MAX_REJECT_FRACTION` (default 0.2) of in-scope rows are rejected, the run fails and
  nothing is stored.
- Re-loading identical data changes nothing. A changed value is updated in place, marked `revised`,
  and the previous value is kept in `observation_revisions`.
- Run status: succeeded, partial, failed, skipped_unchanged, skipped_disabled, skipped_already_running.
  Only one live run per source; runs abandoned for more than 6 hours are failed automatically.

**First real run checklist.** (1) Run `--dry-run` and read the header/row-count report. (2) Confirm the
WHO data licence yourself; it is unverified and `allow_public_export` is deliberately unset. (3) Only then
enable the source (`update public.data_sources set enabled = true ...`) and set `terms_verified_at`.

## Security model

- **RLS enabled on every table.** Client roles (`anon`, `authenticated`) start with no privileges; migration 0005 grants only what is listed.
- **Public data** (sources, geographies, observations, approved analyses, approved documents) is readable by everyone.
  Analyses awaiting/failing review and unapproved documents are not.
- **User data** (profiles, notification preferences/history): a user reads only their own rows. Notification tables are
  read-only for clients; writes go through this API, which validates consent and email ownership. Clients may update only
  presentational profile columns.
- **Operational tables** (ingestion runs, jobs, evaluations, webhook events, roles) have RLS and *no* client policies.
- **Defence in depth in the API:** user endpoints run in a transaction with `SET LOCAL ROLE authenticated` and the verified
  JWT claims, so Postgres itself enforces isolation even if a query forgot a `WHERE user_id = …`. Public endpoints use role `anon`, read-only.
- **JWTs** are verified server-side: ES256/RS256 via the project JWKS, or HS256 with `SUPABASE_JWT_SECRET` (legacy projects).
  Algorithm allow-list, audience `authenticated`, issuer check, `exp`/`sub` required; anon/service-role/anonymous-user tokens are rejected.
- **Hardening to do before production:** create a dedicated least-privilege Postgres role for the API instead of `postgres`
  (it must be able to `SET ROLE anon, authenticated`), and keep `SUPABASE_SERVICE_ROLE_KEY` out of any browser bundle.

## Connecting the Lovable frontend

The browser needs only the project URL, the **anon** key, and this API's base URL. Never the service-role key.

```ts
const { data: { session } } = await supabase.auth.getSession();
const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/api/v1/me`, {
  headers: { Authorization: `Bearer ${session?.access_token}` },
});
```

Add every frontend origin you use (Lovable preview, published domain, custom domain) **explicitly** to `ALLOWED_ORIGINS`
(comma-separated). Wildcards are rejected at startup. Errors always look like
`{"error": {"code": "...", "message": "...", "request_id": "..."}}`.

## Design decisions worth knowing

- Missing data is `data_status = 'missing'` with NULL values; CHECK constraints stop it being stored as 0.
- Re-ingestion upserts on a natural key (`NULLS NOT DISTINCT`); changed values are kept in `observation_revisions` by trigger.
- Indicators carry an `indicator_type` (clinical, positivity, wastewater, …) so incompatible types are never compared silently.
- Population requires a year **and** a source; none are seeded. All sources are seeded **disabled** with unverified cadence/licence.
- Documents are retrievable only when `review_status = 'approved'`; `authority_level` separates official guidance from research.
- Notification history is unique on `(user_id, deduplication_key)` so retries cannot double-send.
- Embeddings are `vector(768)`; changing to a different dimension needs a new migration plus re-embedding.

## Analysis and surveillance API (Phase 4)

```bash
python -m app.cli analyze --dry-run          # compute and report, store nothing
python -m app.cli analyze                    # store results (idempotent; safe to re-run)
python -m app.cli analyze --as-of 2026-10-30 --json
```

Run `analyze` after each ingestion. It exits 0 even when it flags series: a statistical flag is not a
failure. Full method, thresholds and limitations: [docs/methodology_analysis.md](docs/methodology_analysis.md).
Endpoints, rules and samples: [docs/api_surveillance.md](docs/api_surveillance.md).

In short: only rate indicators with a denominator are analysed (positivity, not raw counts); blank
weeks are skipped, never zero; tiny samples and stale data can never raise an alert; a flag needs both
statistical and practical significance; the strongest status is `possible_anomaly`, which never means
an outbreak; cross-country comparison is side-by-side only, rates only, one sampling stream at a time.

Apply migration `20261029000009` before running `analyze` (`supabase db push`).

## Status

Verified means "actually executed in the build sandbox"; the sandbox had no PyPI access and could not reach who.int.

| Area | State |
|---|---|
| Migrations 0001-0009 | **Applied and tested** on PostgreSQL 16; `tests/db/checks.sql` passes (including 0009, and shown to fail when the key is wrong) |
| Ingestion SQL, analysis SQL, surveillance SQL (as roles `anon` / `service_role`) | **Tested on real PostgreSQL 16**: 66 tests in `tests/db/` |
| Statistics, analysis engine, loading, service, CLI parsing | **Tested**; engine checked by 15 deliberate breakages, all caught |
| Response builders (comparison refusals, live staleness, alert withholding, JSON shape) | **Tested**; checked by 14 deliberate breakages, all caught |
| FluNet parser, connector, runner, ingestion CLI | **Tested** with fakes for HTTP and the database |
| Routers (`/surveillance/*`, `/sources/*`) | **Written, never run**: FastAPI could not be installed. A static test proves each router passes exactly the parameters its SQL declares. |
| `SqlIngestionRepository`, `SqlAnalysisRepository`, `Database`, httpx fetcher | **Written, never run** (sqlalchemy/asyncpg/httpx not installable); the SQL they run is tested |
| Phase 1 FastAPI app and its tests | **Written, never run**. Run `pytest -q` first on a machine with the dependencies. |
| Whole suite in the sandbox | 463 tests pass; the three Phase 1 files needing `fastapi` / `pydantic_settings` could not be collected and were excluded; `ruff check` and `ruff format --check` clean |
| Real FluNet download | **Not verified** (who.int unreachable). The parser rejects a header that differs from the WHO dictionary. |
| Detection accuracy | **Not evaluated**: no real history ingested; thresholds are conventional, untuned |
| WHO data licence | **Unverified**; source seeded disabled, export flag unset |
| pgvector column / `match_document_chunks()` | **Written, not executed** locally; CI covers it |
| Supabase project, Gemini, Resend | Not connected: no credentials |
| Dataset provenance/validation (`app/datasets/`), NIH Table-1 parser | **Tested** on the real supplied files (see `docs/DATA_EXPANSION_REPORT.md`); not yet wired to a loader that writes `observations` |
| Email scheduling (`app/notifications/`: schedule, planner, dispatcher, Resend sender, Svix webhook) | **Tested** with fakes and, for the SQL/migration, on real PostgreSQL 16; never run against a live Resend account |
| Phase 5 (RAG + Gemini), Phase 7 NIH/CDC live connectors, Phase 8-9 | Not started |

## Roadmap

3 WHO FluNet (done) → 4 analysis engine + surveillance APIs (done) → data expansion + email
scheduling (done, see `docs/DATA_EXPANSION_REPORT.md`) → 5 RAG + Gemini →
6 Resend digests/alerts (scheduling/sending built; live account not connected) →
7 NIH + CDC live connectors (parser built; live ingestion not wired) →
8 frontend integration docs → 9 evaluation, exports, deployment.

The master spec (`Prompt.txt`) defines **9 phases** in total: 1 skeleton, 2 schema/RLS/auth,
3 WHO FluNet ingestion, 4 analysis + surveillance API, 5 RAG + Gemini, 6 Resend
notifications, 7 NIH + CDC connectors, 8 frontend integration docs, 9 evaluation/exports/deployment.
