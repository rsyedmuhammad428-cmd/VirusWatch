from __future__ import annotations

from datetime import date

import pytest

from app.analysis.service import AnalysisSummary
from app.cli import build_parser, format_analysis_summary


def test_analyze_defaults():
    args = build_parser().parse_args(["analyze"])
    assert (args.command, args.as_of, args.dry_run, args.json) == ("analyze", None, False, False)


def test_analyze_options():
    args = build_parser().parse_args(["analyze", "--as-of", "2026-10-30", "--dry-run", "--json"])
    assert args.as_of == date(2026, 10, 30) and args.dry_run and args.json


def test_a_malformed_date_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["analyze", "--as-of", "30/10/2026"])


def make_summary(**kw):
    return AnalysisSummary(
        as_of=date(2026, 10, 30),
        series_found=2,
        series_analysed=1,
        series_skipped=["XX influenza influenza_positivity: only weekly (7-day) periods"],
        results_submitted=6,
        results_inserted=4,
        status_counts={"normal": 1},
        **kw,
    )


def test_summary_reports_counts_and_skips():
    text = format_analysis_summary(make_summary())
    assert "2 found, 1 analysed, 1 skipped" in text
    assert "6 computed, 4 new, 2 already stored" in text
    assert "skipped       XX influenza" in text
    assert "FLAG" not in text


def test_flags_are_labelled_as_statistical_not_outbreaks():
    text = format_analysis_summary(make_summary(flagged=["possible_anomaly: PK influenza x"]))
    assert "FLAG" in text and "not a confirmed outbreak" in text


def test_dry_run_is_stated():
    assert "dry run: nothing stored" in format_analysis_summary(make_summary(dry_run=True))
