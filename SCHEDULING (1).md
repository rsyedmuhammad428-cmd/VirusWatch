# Scheduling and background jobs (Prompt.txt §14)

Status: **documented, not deployed.** No Supabase project is connected in this engagement (no
credentials were available in the build sandbox), so nothing below has been applied to a live
`cron.schedule()` call or a live Edge Function. This is the honest configuration plan and the
mapping from each required job to the code that already exists and is tested (with fakes and,
where noted, real PostgreSQL) to run it.

## Why Supabase Cron + Edge Functions, and where that breaks down

Supabase Cron (`pg_cron` + `pg_net`) is the right fit for **lightweight, short, mostly-SQL
triggers**: scheduling an Edge Function call every few minutes, with a hard execution ceiling
(Edge Functions time out well under a minute of wall-clock CPU time on the default plan, and
`pg_cron`'s own job runner is not meant for long-running work). It is the **wrong** fit for:

- **Heavy PDF parsing** (`pdftotext -layout` over dozens of pages per NIH bulletin, or OCR on a
  scanned source later) — CPU- and memory-heavier than an Edge Function's isolate is built for.
- **Historical backfills** (`ingest --backfill`, or a first `ingest-documents` run over the whole
  catalog) — can run for minutes, well past typical Edge Function time limits.
- **Statistical/analysis work** over a growing observations table — fine today, but not something
  to assume stays within an Edge Function's ceiling as data volume grows.

Per §14's own guidance ("use a separate Python worker or scheduler if required"), this project's
plan is: **Supabase Cron triggers the short/cheap jobs directly; the heavy jobs are triggered by
Supabase Cron calling a lightweight Edge Function that enqueues or pings an external worker**,
which is the already-built `python -m app.cli <command>` process, run on a schedule by whatever
Python-capable scheduler/host the operator chooses (a small VM, a Fly.io/Railway cron job, or a
GitHub Actions scheduled workflow hitting the worker over SSH/API — not decided here, since no
hosting account was provided).

## The 8 required jobs

| # | §14 job | How it runs today | Idempotent / safe to retry |
|---|---|---|---|
| 1 | Source checks | `python -m app.cli sources` lists registered sources and whether a connector exists; no live health-ping endpoint exists yet (gap, see Limitations) | Read-only, trivially safe |
| 2 | New-data ingestion | `python -m app.cli ingest --source <slug> [--backfill \| --since-year N]` (Phase 3/7) | Yes — checksum-based skip-if-unchanged (`RunStatus.SKIPPED_UNCHANGED`); natural-key upsert means a re-run of the same file changes nothing |
| 3 | Source-document ingestion | `python -m app.cli ingest-documents --source <who\|nih\|cdc\|pubmed\|all>` (Phase 5) | Yes — `app.rag.pipeline.ingest_source` skips re-storing an unchanged checksum (`status="updated_unchanged_checksum"`) |
| 4 | Surveillance analysis | `python -m app.cli analyze [--as-of DATE]` (Phase 4) | Yes — analysis rows are keyed by series + period; re-running the same `--as-of` recomputes, it does not duplicate |
| 5 | Digest dispatch | `python -m app.cli notify --mode auto` (Phase 6, this phase) | Yes — `UNIQUE (user_id, deduplication_key)` on `notification_history` blocks a double-send for the same delivery period even across overlapping/retried runs |
| 6 | Alert dispatch | Same `notify` command — digests and alerts are planned and sent in one cycle (`app.notifications.dispatcher.run_cycle`) | Yes — same dedup guard, keyed `alert:<analysis_result_id>` |
| 7 | Data-freshness monitoring | The `source_freshness` view (migration `20261029000006_functions_and_views.sql`, service_role only) exists for an operator/dashboard to query; no scheduled job pages anyone yet (gap, see Limitations) | Read-only view, safe |
| 8 | Cleanup of eligible temporary files | Not implemented (gap, see Limitations) — no temp files are currently written outside the process's own working directory that would need cleanup | N/A yet |

## Proposed `cron.schedule()` configuration (not yet applied)

```sql
-- Supabase SQL editor, once a project exists. Each job calls a thin Edge Function that either
-- does the (cheap) work itself or pings the external worker for the heavy jobs (3, 4 at scale).
select cron.schedule('vw-ingest-flunet-daily',   '15 3 * * *', $$select net.http_post(url := '<edge-fn>/ingest', body := jsonb_build_object('source','who_flunet'))$$);
select cron.schedule('vw-ingest-documents-daily','30 3 * * *', $$select net.http_post(url := '<edge-fn>/ingest-documents', body := '{"source":"all"}')$$);
select cron.schedule('vw-analyze-daily',         '0 4 * * *',  $$select net.http_post(url := '<edge-fn>/analyze')$$);
select cron.schedule('vw-notify-every-15-min',   '*/15 * * * *', $$select net.http_post(url := '<edge-fn>/notify', body := '{"mode":"auto"}')$$);
select cron.schedule('vw-freshness-check-hourly','0 * * * *', $$select net.http_post(url := '<edge-fn>/freshness-check')$$);
```

Notes on the choices above (to be revisited once a real project and traffic pattern exist, not
treated as final):
- `notify` runs every 15 minutes, not once a day, because alert dispatch (job 6) is meant to fire
  promptly after a new validated anomaly, while digest dispatch (job 5) is naturally throttled by
  each user's own `scheduled_for`/dedup key — running the cycle often costs nothing extra.
- Daily ingestion/analysis times are staggered (03:15, 03:30, 04:00) so analysis always sees that
  day's freshly ingested data, not a half-finished ingestion run.
- None of the `<edge-fn>` URLs exist yet; this table documents intent, not a deployed schedule.

## Hosting-tier limitations (stated, not worked around)

- Supabase's free/low tiers may not include `pg_cron`/`pg_net` at all, or cap job frequency; the
  schedule above assumes a tier that has both enabled.
- Edge Function wall-clock/memory limits mean jobs 2 and 3 should, past a small catalog/backfill
  size, be triggered (not executed) by the Edge Function — forwarded to the external worker.
- No external worker host is set up in this engagement (no hosting credentials provided); the
  `python -m app.cli` commands are the worker's actual implementation, ready to be scheduled by
  whatever host is chosen.

## Known limitations and gaps (honest, not deferred silently)

- Job 1 ("source checks") has no live reachability/health-check command yet — `sources` lists
  configuration, not a live ping of each source's URL. A `python -m app.cli check-sources` command
  that HEADs/GETs each enabled source's URL and records the result would close this; not built
  this phase.
- Job 7 ("data-freshness monitoring") has a queryable view but no scheduled job that acts on it
  (e.g., paging an operator, or feeding `source_freshness` into the "last-update timestamp" shown
  in the AI workflow's answers, which already happens per-observation, not via this view).
- Job 8 ("cleanup of eligible temporary files") has nothing to clean up yet: the ingestion and RAG
  pipelines write only to in-memory structures and the database, not to disk. This becomes
  relevant once a real downloader/extractor writes temp files (e.g., very large PDFs staged to
  disk before `pdftotext`); flagged here so it isn't forgotten when that lands.
