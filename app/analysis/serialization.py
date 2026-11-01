"""AnalysisResult <-> the JSON shape consumed by sql.INSERT_RESULTS."""

from __future__ import annotations

from typing import Any

from app.analysis.models import AnalysisResult


def result_to_json(r: AnalysisResult) -> dict[str, Any]:
    return {
        "geography_id": r.key.geography_id,
        "disease": r.key.disease,
        "pathogen": r.key.pathogen,
        "indicator": r.key.indicator,
        "surveillance_stream": r.key.surveillance_stream,
        "analysis_type": r.analysis_type,
        "analysis_period_start": r.period_start.isoformat(),
        "analysis_period_end": r.period_end.isoformat(),
        "baseline_period_start": r.baseline_start.isoformat() if r.baseline_start else None,
        "baseline_period_end": r.baseline_end.isoformat() if r.baseline_end else None,
        "latest_observation_end": (
            r.latest_observation_end.isoformat() if r.latest_observation_end else None
        ),
        "observed_value": r.observed_value,
        "baseline_value": r.baseline_value,
        "effect_size": r.effect_size,
        "score": r.score,
        "threshold": r.threshold,
        "status": r.status,
        "confidence_or_uncertainty": r.confidence_or_uncertainty,
        "methodology_version": r.methodology_version,
        "parameters": r.parameters,
        "missing_data_handling": r.missing_data_handling,
        "limitations": r.limitations,
        "input_observation_ids": list(r.input_observation_ids),
        "input_hash": r.input_hash,
        "explanation": r.explanation,
    }
