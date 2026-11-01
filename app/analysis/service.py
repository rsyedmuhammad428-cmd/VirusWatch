"""Run the analysis engine over every stored series and persist the results."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from app.analysis.engine import UnsupportedSeriesError, analyze_series
from app.analysis.models import AnalysisConfig, AnalysisResult
from app.analysis.ports import AnalysisRepository


@dataclass
class AnalysisSummary:
    as_of: date
    series_found: int = 0
    series_analysed: int = 0
    series_skipped: list[str] = field(default_factory=list)  # "<series>: <reason>"
    results_submitted: int = 0
    results_inserted: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)  # of the anomaly_detection rows
    flagged: list[str] = field(default_factory=list)  # human-readable, never "outbreak"
    dry_run: bool = False

    @property
    def results_already_stored(self) -> int:
        return self.results_submitted - self.results_inserted


def describe(r: AnalysisResult) -> str:
    k = r.key
    stream = f"/{k.surveillance_stream}" if k.surveillance_stream else ""
    return f"{k.country_code} {k.disease} {k.indicator}{stream} week {r.period_start}"


async def run_analysis(
    repo: AnalysisRepository,
    config: AnalysisConfig,
    as_of: date,
    *,
    dry_run: bool = False,
) -> AnalysisSummary:
    """Analyse all series with an indicator rule. A series the engine cannot handle is reported
    in the summary, never silently dropped and never allowed to stop the other series."""
    summary = AnalysisSummary(as_of=as_of, dry_run=dry_run)
    series = await repo.load_series(sorted(config.rules))
    summary.series_found = len(series)

    results: list[AnalysisResult] = []
    for key, points in series:
        try:
            produced = analyze_series(key, points, config, as_of)
        except UnsupportedSeriesError as exc:
            label = f"{key.country_code} {key.disease} {key.indicator}"
            summary.series_skipped.append(f"{label}: {exc}")
            continue
        if produced:
            summary.series_analysed += 1
            results.extend(produced)

    counts: Counter[str] = Counter()
    for r in results:
        if r.analysis_type == "anomaly_detection":
            counts[r.status] += 1
            if r.status in {"elevated", "possible_anomaly"}:
                summary.flagged.append(f"{r.status}: {describe(r)}")
    summary.status_counts = dict(sorted(counts.items()))

    summary.results_submitted = len(results)
    if results and not dry_run:
        outcome = await repo.store_results(results)
        summary.results_inserted = outcome.inserted
    return summary
