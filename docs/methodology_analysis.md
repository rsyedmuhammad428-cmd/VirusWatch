# Statistical surveillance methodology (version 1.0.0)

Implemented in `app/analysis/`. Deterministic: the same stored observations and parameters always
give the same results. No AI is involved. Nothing here has been validated against real outbreak
data; see "What has not been evaluated".

## What is analysed

Only indicators with an explicit rule (`AnalysisConfig.rules`). Today that is `influenza_positivity`
(percent of tested specimens positive, with numerator and denominator). Raw counts are **not**
analysed: without a denominator a change in testing volume looks like a change in disease.
Each series is one (geography, disease, pathogen, indicator, surveillance stream). Sentinel and
non-sentinel data are never merged. Only weekly (7-day) periods are supported; anything else is
reported as skipped, not guessed.

## Inputs and missing data

- A blank value is *missing* and is skipped. It is never treated as zero.
- Weeks flagged `small_denominator` (fewer than 10 tested specimens) are excluded from every
  statistic and can never raise an alert.
- The newest reported week is the "current" week. If it is blank, results are `insufficient_data`.
- Dates in `exclude_ranges` (empty by default) are left out of baselines. Choosing such ranges, for
  example for periods of abnormal circulation, is a scientific decision and is not made in code.

## The six analyses (stored in `analysis_results`)

| Type | What it computes | Needs |
|---|---|---|
| `freshness` | Days from the end of the latest reported period to the run date. `stale_data` above 28 days. | one reported week |
| `rolling_average` | Mean of reported weeks in the last 4. | 3 of 4 weeks |
| `trend` | Theil-Sen slope per week and Mann-Kendall z / two-sided p over the last 8 weeks; direction only if p < 0.10. | 6 of 8 weeks |
| `baseline_comparison` | Current week vs the median of the 8 weeks before it (trailing baseline). | 6 of 8 weeks |
| `seasonal_comparison` | Current week vs the median of the same time of year (+/- 2 weeks) in up to 5 prior years. | 3 years, 12 weeks |
| `anomaly_detection` | Same comparison, using the seasonal baseline when available, otherwise the trailing one. | as above |

**Score.** Robust z = (current - baseline median) / scale, where scale = max(1.4826 x MAD, floor).
The floor (1.0 percentage point for positivity) stops a run of near-identical weeks from turning any
change into an enormous z.

**Status.** A result is `elevated` when z >= 2.0 **and** the rise is at least 2.0 percentage points;
`possible_anomaly` when z >= 3.5 and the same rise. Both conditions are required: statistical and
practical significance. Falls are never flagged. `baseline_comparison` and `seasonal_comparison`
never go beyond `elevated`; only `anomaly_detection` can say `possible_anomaly`.
Other statuses: `insufficient_data` (not enough usable weeks, blank or small-sample current week),
`stale_data` (latest data older than 28 days; no alert is raised from stale data).

**`confirmed_by_source` is never produced by the engine.** It exists in the schema for a human to
record an explicit statement by a credible source, and the database requires a URL for it.

## Reproducibility

Every result stores its parameters, the ids of the observations used, the baseline window, the
methodology version, and `input_hash`, a SHA-256 of (method version, series, parameters, input
observation ids, periods, values, data status, flags). Re-running on unchanged data inserts nothing
(unique key includes the hash). A revised observation or a changed parameter produces new rows and
keeps the old ones. `freshness` includes the run date in its hash, so it adds one row per series
per day. Bump `METHODOLOGY_VERSION` whenever a rule or default changes.

## Limitations (read before relying on any result)

- A trailing baseline cannot distinguish a normal seasonal rise from an unusual one; seasonal
  baselines assume past seasons are representative. Years of abnormal circulation distort them.
- Positivity depends on who is tested and why. It is not incidence.
- Reporting delays and later revisions change results; the newest week is often incomplete.
- Thresholds (2.0, 3.5, 2 points, 1 point floor) are conventional starting values. They have not
  been tuned or evaluated on real data for any country.
- National data say nothing about a city or province.

## What has not been evaluated

Precision, recall, false-alarm rate, detection delay and forecasting error have **not** been
measured: no suitable labelled history has been ingested yet. No claim of early detection or of
validity on foreign datasets is made. Evaluation (rolling-origin splits, no leakage, comparison with
simple baselines) is Phase 9.

## Verification actually performed

Unit tests (statistics checked against hand calculations; every threshold boundary; blank,
small-sample and stale handling; seasonal vs trailing; hash sensitivity), mutation checks (15 targeted
breakages of the engine, all caught), and SQL tests on PostgreSQL 16. All inputs were synthetic.
