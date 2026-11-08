# Scheduling and Background Jobs (Prompt.txt §14)

Status: **documented plan, not applied.** No Supabase project is connected from this sandbox, so
nothing below has been run against a real `pg_cron`/`pg_net` instance. Every job it references
(`python -m app.cli <command>`) is itself built and tested (see `docs/PHASE6_REPORT.md` and
earlier phase reports); what's new here is only the scheduling layer around them.

## Why Supabase Cron + Edge Functions, with heavy jobs pushed to an external worker

Supabase's `pg_cron` (fires on a cron expression, inside Postgres) plus `pg_net` (lets a cron job
make an outbound HTTP call) is the natural way to invoke a Supabase Edge Function on a schedule
without any infrastructure of our own. But Edge Functions run on Deno, with a wall-clock execution
limit (minutes, not hours) and no `pdftotext`, no `httpx` connection pooling across thousands of
rows, and no persistent Python process. Several of our jobs — parsing NIH PDF bulletins, backfilling
years of FluNet history, running `analyze` over the full observation table — are exactly the kind
of job that limit is built to stop. `Prompt.txt` §14 itself anticipates this ("use a separate
Python worker if required"), so the plan below keeps Postgres cron as the *trigger* but routes
anything long-running to the external worker: the same `python -m app.cli <command>` CLI already
built in every earlier phase, run from a machine with Python, outbound network access, and no time
limit (a small VM, a container on a schedule, or a CI cron job — anything that can run
`python -m app.cli notify` on a clock).

## The 8 required background jobs

| # | Job (Prompt.txt §14) | Maps to | Where it runs | Idempotency |
|---|---|---|---|---|
| 1 | Check source health / availability | **Gap.** No command exists yet that pings each configured source URL and records up/down. | — | — |
| 2 | Daily FluNet (or configured source) ingestion | `python -m app.cli ingest flunet` | External worker (network + `pdftotext`/CSV parsing) | Yes — ingestion upserts by natural key, re-running a day twice changes nothing |
| 3 | Daily/weekly document ingestion for RAG | `python -m app.cli ingest-documents` | External worker (network, `pdftotext`) | Yes — checksum-based skip-if-unchanged (`app/rag/pipeline.py`) |
| 4 | Run analysis after each ingestion | `python -m app.cli analyze` | External worker or Edge Function (pure SQL + Python stats, no network) | Yes — `analysis_results` upsert, documented in `docs/methodology_analysis.md` |
| 5 | Dispatch digests (daily 9AM / weekly Monday 9AM / monthly 1st 9AM, Asia/Karachi) | `python -m app.cli notify --kind digest` | Edge Function (short: reads `user_notification_preferences`, enqueues/sends) | Yes — `UNIQUE (user_id, deduplication_key)` in `notification_history`, key embeds the calendar period (`digest:<user>:weekly:2026-W45`) |
| 6 | Dispatch event-triggered alerts | `python -m app.cli notify --kind alert` | Edge Function (short) | Yes — dedup key `alert:<analysis_result_id>`, and `alert_decision()` only fires on `review_status="approved"` by default |
| 7 | Monitor source/data freshness, alert on staleness | **Gap.** The `source_freshness` view (migration `20261031000010`) exists and can be queried, but no job reads it on a schedule and turns a stale source into a notification yet. | — | — |
| 8 | Clean up temp files / expired exports | **Not needed yet.** No command in this codebase currently writes temp files or exports to disk that would need cleanup — `ingest-documents`, `analyze`, and `notify` all read/write directly to PostgreSQL, nothing lands on a local filesystem that an external worker wouldn't already discard between runs. Revisit when Phase 9 (exports) adds on-disk export files. | — | — |

## Proposed `cron.schedule()` configuration (not applied)

This is what would be run once a Supabase project exists, to trigger the two Edge-Function-sized
jobs (digests and alerts) directly from Postgres via `pg_net`. The external-worker jobs (1-4) are
scheduled on the worker's own clock (a system cron entry or CI schedule calling the CLI), not from
inside Postgres, since Postgres can't run arbitrary Python.

```sql
-- Requires: create extension if not exists pg_cron; create extension if not exists pg_net;
-- Times are UTC in cron.schedule() itself; Asia/Karachi is UTC+05:00 with no DST, so a
-- 9:00 AM Asia/Karachi digest is a fixed 04:00 UTC cron trigger. The per-user digest TIME
-- the person actually chose (DeliverySchedule) is evaluated inside the dispatcher, in their
-- own timezone, against the calendar date — this cron entry just has to run at least once
-- during every relevant hour; running it hourly and letting the planner decide who's due
-- avoids re-deriving UTC offsets for every timezone a user might pick.
select cron.schedule(
  'viruswatch-notify-hourly',
  '0 * * * *',  -- every hour, on the hour (UTC)
  $$
  select net.http_post(
    url := 'https://<project-ref>.supabase.co/functions/v1/notify-dispatch',
    headers := jsonb_build_object('Authorization', 'Bearer ' || current_setting('app.service_role_key')),
    body := '{}'::jsonb
  );
  $$
);
```

The `notify-dispatch` Edge Function itself is not written here — it would be a thin HTTP wrapper
calling the same `plan_digest()` / `alert_decision()` / dispatcher functions already in
`app/notifications/`, which is why those functions are plain, dependency-light Python rather than
FastAPI-route-shaped: the same logic needs to run from a CLI, (eventually) an Edge Function, and
the test suite without change.

## Hosting-tier limitations

- Supabase's free and lower paid tiers cap Edge Function execution time and `pg_cron` job
  frequency; an hourly trigger is well within free-tier limits, but a sub-hourly one may not be.
- `pg_net` calls are fire-and-forget from Postgres's perspective — the cron job doesn't see the
  Edge Function's response or retry on failure. Retry/backoff has to live in the dispatcher logic
  itself (already true today: `app/notifications/dispatcher.py`'s per-row status tracking means a
  failed send is visible and re-driveable on the next run, not silently dropped).
- The external worker for jobs 1-4 needs its own always-on or scheduled compute (a VM, a
  container host, or CI minutes) — nothing in Supabase's own tiers runs long Python jobs.

## Explicit gaps (not implemented)

- **Job 1 (source health-checks):** no command pings `https://www.tycho.pitt.edu`,
  `https://www.who.int`, etc. and records reachability. Would be a new, small CLI subcommand
  (`python -m app.cli check-sources`) writing to a new or existing table; not built this phase.
- **Job 7 (freshness alerting):** `source_freshness` (a view, already migrated) can answer "how
  stale is each source right now," but nothing turns that into a notification on a schedule. This
  would reuse the same `notification_history`/dedup pattern as alerts, keyed by source rather than
  by `analysis_result_id`.
- **Job 8 (temp-file cleanup):** no-op today, see the table above — nothing currently writes temp
  files. Revisit alongside Phase 9's export feature.
