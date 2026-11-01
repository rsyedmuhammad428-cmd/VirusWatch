# Surveillance and sources API (Phase 4)

Base path `/api/v1`. Public endpoints need no token and run as the database role `anon`, so
row-level security decides what is visible. Errors use the standard envelope
`{"error": {"code", "message", "request_id"}}`.

**Status: written but not yet run.** FastAPI could not be installed in the build environment, so the
HTTP layer has never been started. The SQL (against PostgreSQL 16), the response builders and the
router/SQL parameter contract are tested. The samples in `docs/samples/` were produced by running
those real pieces on the **synthetic** fixture, not by calling a server.

Every response has `meta.generated_at` and `meta.notices`. Numeric values are JSON numbers; `null`
means "not reported" and is never zero. Every series states its `geographic_resolution` and a
`resolution_note` (national data must never be read as a city or province).

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /surveillance/geographies` | public | Geographies that have data, with resolution and coverage |
| `GET /surveillance/observations` | public | Stored observations, paginated (`limit` 1-1000, `offset`), newest first |
| `GET /surveillance/latest` | public | Newest value per series + live freshness + latest screening `signal` |
| `GET /surveillance/trends` | public | Chronological points per series + stored trend and rolling average |
| `GET /surveillance/alerts` | public | Fresh series whose latest screening is `elevated` / `possible_anomaly` |
| `GET /surveillance/comparisons` | public | Side-by-side positivity for 2-10 countries (rates only) |
| `GET /sources` | public | Sources, cadence, and whether their terms have been reviewed |
| `GET /sources/status` | public | Last successful run and newest period per source (no error text) |
| `GET /sources/{id}/runs` | admin | Ingestion run history including error summaries |

Common filters: `country` (ISO alpha-2, e.g. `PK`), `disease`, `indicator`, `stream`
(`sentinel`/`nonsentinel`/`undefined`), `date_from`, `date_to` (periods overlapping the range).

## Rules the API enforces

- **Signal.** `latest.signal` is the latest anomaly screening: `normal`, `elevated`,
  `possible_anomaly`, `insufficient_data`, `stale_data`; or `not_analysed` (no result yet) or
  `analysis_outdated` (the screened week is not the newest observed week). It is a statistical flag,
  never a confirmed outbreak, and each alert carries a disclaimer.
- **Staleness is computed live** from today's date (`freshness.data_age_days`, `is_stale`, limit 28
  days, the same constant the engine uses). A flagged result whose data have since gone stale is
  withheld from `/alerts` and counted in `meta.notices`.
- **Comparisons** are refused with HTTP 422 unless the indicator is a rate with an explicit
  denominator (`influenza_positivity`). Counts return `indicator_not_comparable`; fewer than two
  countries returns `too_few_countries`; mixed units or indicator types are refused. One sampling
  stream per request (default `sentinel`). Values are aligned by week with `null` gaps. **No
  difference, ranking or score between countries is ever computed.** Caveats about testing practices,
  delays, resolution and sources are always returned. A country with no data is listed in
  `countries_without_data`, never filled in.
- **Attribution.** Every observation carries `source` (name, publisher, `license_name`,
  `terms_verified`) and `source_url`. WHO FluNet's licence is currently unverified
  (`terms_verified: false`).

## Decision for you to review: display vs export

These endpoints *display* attributed statistics. The `data_sources.allow_public_export` flag, still
false for FluNet, is intended to gate **bulk export** (`/research/data-export`, Phase 9), not
display. If you consider display of unverified-licence data unacceptable, the fix is one RLS policy
change on `observations`; say so and I will make it.

## Samples (synthetic fixture output)

`docs/samples/latest.json`, `alerts.json`, `comparisons.json`, `geographies.json`.
Note that the latest fixture week is `insufficient_data`: the fixture is too short for a baseline.

## Not yet built

`/ai/*` (Phase 5), `/me/*` extensions and notifications (Phase 6), `/research/*` and admin
endpoints (Phase 9). Rate limiting and response caching are not implemented.
