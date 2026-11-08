# Phase 6 — Email (Resend) — completion report

Date: 2026-11-02 · Scope: `Prompt.txt` §13 (Email Notification Engine), §14 (Scheduling and
Background Jobs), and the Phase-6-relevant parts of §15 (`/api/v1/me/notifications*`) · Status:
**implemented and tested with fakes/real PostgreSQL; never sent a real email (no Resend
credentials, no verified sending domain, no live run)**

This report follows the 8-point structure required after every phase (`Prompt.txt` §23).

## 0. What was and was not done

Most of §13's scheduling/dispatch/delivery-tracking machinery (`app/notifications/`) was already
built and tested during the earlier "Data Expansion & Email Scheduling" request, because that
request's own Part 3 ("Email Scheduling") covered the same ground as this phase. This phase's
actual new work is what that earlier pass left out: the **API-level** test-send/history endpoints
the master spec names explicitly under §15, a shared/tested sender-selection policy, and the §14
scheduling documentation that did not exist yet.

| Done this phase | Not done this phase |
|---|---|
| `GET /api/v1/me/notifications` (settings + recent delivery history) and `POST /api/v1/me/notifications/test` (the spec's named "Implement a test digest" endpoint, §15) | Running either endpoint (FastAPI not installable here, same limitation as every router since Phase 1) |
| Extracted `app.cli._choose_sender`'s dev/live sender policy into a shared, directly-tested `app.notifications.sender.choose_sender()`, used by both the CLI and the new API endpoint | Sending a real email through it — no Resend API key or verified domain available |
| 3 new SQL constants (`GET_PREFERENCES`, `HISTORY`, `INSERT_TEST_SEND`) and matching repository methods, covered by the static SQL-contract scanner and 4 new real-PostgreSQL tests | A domain-verification *check* against Resend's API (`resend_domain_verified` is a manually-set config flag, not something this code queries live — see §9) |
| `docs/SCHEDULING.md`: the §14 job-by-job mapping, a proposed (not applied) `cron.schedule()` configuration, and stated hosting-tier limitations | Applying any of it — no Supabase project is connected |
| 8 new tests (`tests/notifications/test_sender_factory.py`, 4 in `tests/db/test_notifications_sql.py`) + fixed a pre-existing timezone-fragile assertion in `test_digest_and_alert_items_carry_source_link_and_uncertainty` (it compared a `timestamptz` string by prefix, which broke when this sandbox's `TZ` changed to `America/Los_Angeles` between sessions — now compares as an aware `datetime`) | — |

## 1. Files created or modified

**New:**
- `docs/SCHEDULING.md` — §14.
- `tests/notifications/test_sender_factory.py` — 8 tests for `choose_sender()`'s auto/live/dev branches and fail-closed behavior on missing credentials.
- This file.

**Modified:**
- `app/notifications/sender.py` — added `choose_sender()` (moved and generalized from `app/cli.py`'s private `_choose_sender`, which now delegates to it).
- `app/cli.py` — `_choose_sender` is now a 10-line wrapper around the shared function; fixed one pre-existing line-length lint error in `notify`'s `--redirect-to` help text while touching the file.
- `app/notifications/ports.py` — added `HistoryEntry` (the shape `GET /me/notifications` returns per history row).
- `app/notifications/sql.py` — added `GET_PREFERENCES`, `HISTORY`, `INSERT_TEST_SEND`.
- `app/notifications/sql_repository.py` — added `get_preferences()`, `history()`, `record_test_send()`.
- `app/api/v1/notifications.py` — moved `PUT .../preferences` to `/api/v1/me/notifications/preferences` (see §7 for the path-naming note); added `GET /api/v1/me/notifications` and `POST /api/v1/me/notifications/test`.
- `tests/db/test_notifications_sql.py` — 4 new tests for the 3 new SQL constants; fixed the timezone-fragile assertion noted above.
- `README.md` — Status table and Roadmap updated.

## 2. Features implemented (mapped to spec)

**§13.A Scheduled digest** — unchanged from the earlier pass, re-verified this phase: `plan_digest()` honors frequency/timezone/consent, `digest.render()` includes geography, disease, indicator, reporting period, observed value, baseline, unit, uncertainty (`analysis_results.limitations`), source link, and last-update time, and says "No indicators match your selections right now" rather than inventing content when there's nothing to report. It never claims a weekly report reflects today's conditions: the subject line is dated, not claimed current.

**§13.B Event-triggered alert** — unchanged, re-verified: `alert_decision()` requires consent, opt-in, a severity the method actually defines (`elevated`/`possible_anomaly` only), `review_status in accepted_reviews` (human-approved by default — `DEFAULT_ACCEPTED_REVIEWS = {"approved"}`; an operator can opt into `auto` via `ALERT_ACCEPT_UNREVIEWED`, but approval is the fail-closed default), data not stale, and not a small sample. Deduplication is `UNIQUE (user_id, deduplication_key)` in the database, not scheduler memory, so two overlapping runs or a restart cannot double-send. Every attempt and the provider's message id are recorded in `notification_history`.

**Resend integration, dev mode, domain verification (§13)** — `ResendSender` posts to `https://api.resend.com/emails`; `LoggingSender` is the "safe development mode" (suppresses entirely, or redirects to one address, and never logs the HTML/text body — only recipient and subject — in suppressed mode). `choose_sender()` (new this phase, shared by the CLI and the new API endpoint) is the single fail-closed policy: live delivery only happens in `mode="live"` (an explicit operator override) or `mode="auto"` when `environment == "production"` **and** `resend_domain_verified` is `True`. Everywhere else, nothing reaches a real inbox. A message is only ever marked `"sent"` once the provider accepts it, and only moves to `"delivered"` on a verified webhook event — never implied by API success alone (`app/notifications/status.py`'s one-way state machine, `app/notifications/dispatcher.py`'s `mark_sent` vs. the webhook-driven `mark_delivered` path).

**§15 `GET /api/v1/me/notifications`** (new this phase) — returns the caller's own settings (even while disabled — a user must be able to see "notifications are off," not get a 404) and their last 20 `notification_history` rows (type, subject, status, attempt count, timestamps, error summary; never the email body, which isn't stored).

**§15 `POST /api/v1/me/notifications/test`** (new this phase — the spec's named "Implement a test digest") — requires a verified email; builds a message with **no real surveillance data in it at all** (a fixed, clearly-labeled "this is a test, not a digest or alert" message), sent through the same `choose_sender()` policy as the scheduled dispatcher, and records the outcome via `record_test_send()`. The content is deliberately numbers-free so this endpoint can never itself be the way a "public-health claim" (§13) reaches a user — it only proves the pipeline (preferences → sender → delivery tracking) is wired correctly, which is what the spec's one-line Phase 6 summary ("Implement a test digest") asks for.

**§14 Scheduling and Background Jobs** — documented in `docs/SCHEDULING.md`: all 8 required jobs mapped to either an existing `python -m app.cli <command>` (ingest, ingest-documents, analyze, notify) or a stated gap (source health-checks and freshness-alerting have no scheduled job yet, temp-file cleanup has nothing to clean up yet — see that file's own limitations section), a proposed (unapplied) `cron.schedule()` configuration, and why heavy jobs (PDF parsing, backfills) need an external worker rather than an Edge Function, per the spec's own "use a separate Python worker... if required."

## 3. Commands to run

```bash
# Tests (unchanged invocation):
export TEST_PG_ADMIN_CONN="host=/path/to/socket/dir port=5432 user=postgres dbname=postgres"
NO_PGVECTOR=1 python -m pytest -q
ruff check . && ruff format --check .

# Once Resend credentials exist:
python -m app.cli notify --mode dev --redirect-to you@example.test   # safe: never reaches real users
python -m app.cli notify --mode auto                                  # live only in prod + verified domain
# via the API, once FastAPI is running:
curl -X POST .../api/v1/me/notifications/test -H "Authorization: Bearer <jwt>"
curl .../api/v1/me/notifications -H "Authorization: Bearer <jwt>"
```

## 4. Environment variables needed

Unchanged from the earlier pass, now exercised by the new `choose_sender()` path too:
`RESEND_API_KEY`, `EMAIL_FROM`, `RESEND_WEBHOOK_SECRET`, `FRONTEND_BASE_URL`,
`RESEND_DOMAIN_VERIFIED` (bool; see §9 — this is a manually-set flag, not a live API check),
`ALERT_ACCEPT_UNREVIEWED` (bool, default off).

## 5. Database migrations applied or pending

No new migration. `notification_history.notification_type` already allowed `'test'` (migration
`20261029000004`, written before this phase) — this phase's `INSERT_TEST_SEND` is the first code
to use that value. The 4 new tests in `tests/db/test_notifications_sql.py` run against real
PostgreSQL 16 via `NO_PGVECTOR=1` (unrelated to pgvector; this table has none).

## 6. Tests run and their actual outcomes

```
NO_PGVECTOR=1 TEST_PG_ADMIN_CONN=... python -m pytest -q
  --ignore=tests/test_auth.py --ignore=tests/test_health_and_errors.py --ignore=tests/test_config.py
→ 788 passed, 4 skipped
ruff check .          → All checks passed!
ruff format --check . → 159 files already formatted
```

Of the 788: 12 are new this phase (`choose_sender`: 8 tests; the 3 new SQL constants: 4 tests),
one pre-existing test was fixed (not added) for a timezone-fragile assertion, the rest are the
unchanged Phase 1-5 suite re-run to confirm no regression. The 3 excluded files/modules are the
same pre-existing `fastapi`/`structlog`/`pydantic-settings` gap as every previous phase.

**What "tested" does and does not mean here, concretely:**
- `choose_sender()`: fully tested, all branches (auto in/out of production, with/without a
  verified domain, explicit live/dev override, redirect-to, and the two fail-closed `ValueError`
  cases for missing credentials) — this is pure decision logic with no I/O.
- `get_preferences`, `history`, `record_test_send`: the SQL is tested against real PostgreSQL 16
  (constraints, dedup-key independence of test sends from real digests/alerts, status transitions).
  The Python repository methods that wrap that SQL (same shape as every other repository method in
  this codebase) are not separately re-tested — the SQL contract test statically verifies they
  pass exactly the parameters each statement declares.
- `GET /api/v1/me/notifications`, `POST /api/v1/me/notifications/test`: never run. No FastAPI
  TestClient exists in this sandbox for any router, same as `/surveillance/*`, `/sources/*`,
  `/ai/ask` before it. `tests/test_router_sql_contract.py`'s static scanner covers
  `surveillance.py`/`sources.py` specifically (the `fetch(conn, queries.X, ...)` idiom); it does
  not cover `notifications.py` or `ai.py`, because neither calls `conn.execute`/`fetch` directly —
  both delegate to a repository class, which is what the SQL-contract test (not the router one)
  actually checks. This was already the honest state after Phase 5 and remains so.
- Resend/webhook integration: unit-tested against locally generated HMAC signatures (`webhook.py`)
  and a fake HTTP poster (`sender.py`); never exercised against a real Resend account or a real
  webhook delivery.

## 7. A naming deviation from §15, stated plainly

`Prompt.txt` §15 lists `GET /api/v1/me/notifications`, `POST /api/v1/me/notifications/test`, and
`POST /api/v1/me/notifications/unsubscribe` all under `/me`. This phase moved the preferences
endpoint and added the two new ones under `/me/notifications/*` as listed — **except**
unsubscribe, which stays at `POST /api/v1/notifications/unsubscribe` (no `/me`, no
authentication), unchanged from the earlier pass. Reasoning: an unsubscribe link in an email has
to work for a recipient who is not logged in on that device — the per-user secret
`unsubscribe_token` *is* the authentication, the same pattern virtually every bulk-mail sender
uses. Putting it under `/me` would have implied a login requirement that breaks the feature it's
meant to serve. This is a deliberate, documented departure from the literal path list, not an
oversight.

## 8. Manual steps requiring your account or credentials

- A Resend account, API key, verified sending domain, and the webhook secret, to ever send or
  receive anything for real.
- Setting `RESEND_DOMAIN_VERIFIED=true` only after confirming domain verification in the Resend
  dashboard yourself — this code does not call Resend's domain-verification API to check that
  automatically (see Limitations).
- A Supabase project with `pg_cron`/`pg_net` (or an external scheduler/worker host) to actually
  run any of `docs/SCHEDULING.md`'s proposed schedule.
- Installing `fastapi`/`sqlalchemy`/`asyncpg`/`httpx`/`pydantic-settings`/`structlog` on a machine
  with PyPI access, then running the full suite there — this sandbox cannot.

## 9. Known limitations and next phase

- **Domain verification is a manual flag, not a live check.** `resend_domain_verified` must be
  set by a human after confirming in Resend's dashboard; nothing here calls Resend's API to verify
  it automatically. A future pass could add a periodic check (job 1/7 in `docs/SCHEDULING.md`)
  that queries Resend's domain-status endpoint and flips this flag, rather than trusting it blind.
- §14 jobs 1, 7, and 8 (live source health-checks, freshness alerting, temp-file cleanup) remain
  documented gaps, not implemented — see `docs/SCHEDULING.md`'s own limitations section for why
  each one is deferred rather than faked.
- Nothing in `app/notifications/` has ever sent a real email or received a real webhook. Every
  claim above is "tested with fakes/real Postgres," never "verified against Resend."
- Per `Prompt.txt`'s roadmap, Phase 7 (live NIH/CDC connectors) is next; the ingestion/RAG
  connector shape already established in Phases 3, 5, and 7's own audit notes is the pattern to
  extend for live HTTP sources.
