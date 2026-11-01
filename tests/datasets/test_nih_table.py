from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.datasets.nih_table import parse_province_tables

TEXT = Path("tests/fixtures/datasets/nih_week26_table1.txt").read_text(encoding="utf-8")


def value(parse, disease, province):
    return next(c for c in parse.counts if c.disease == disease and c.province == province)


def test_real_week26_table_parses_every_row_and_province():
    p = parse_province_tables(TEXT)
    assert not p.rejected
    assert len(p.totals) == 23 and len(p.counts) == 23 * 7
    assert {c.year for c in p.counts} == {2026} and {c.week for c in p.counts} == {26}


def test_priority_diseases_as_printed():
    p = parse_province_tables(TEXT)
    assert value(p, "dengue", "Sindh").value == Decimal(28)
    assert value(p, "malaria", "Sindh").value == Decimal(40159)
    assert value(p, "measles", "KP").value == Decimal(284)


def test_nr_is_missing_never_zero():
    p = parse_province_tables(TEXT)
    punjab = value(p, "dengue", "Punjab")
    assert punjab.value is None and punjab.flags == ("not_reported",)
    assert value(p, "dengue", "GB").value == Decimal(0)  # a reported zero stays a zero


def test_total_is_flagged_as_excluding_non_reporting_provinces():
    p = parse_province_tables(TEXT)
    total = next(t for t in p.totals if t.disease == "dengue")
    assert "total_excludes_non_reporting" in total.flags
    assert "Punjab" in total.non_reporting_provinces and "AJK" in total.non_reporting_provinces
    assert total.printed_total == Decimal(54) == total.sum_of_reporting


def test_printed_total_that_disagrees_with_its_parts_is_flagged():
    p = parse_province_tables(TEXT)
    ad = next(t for t in p.totals if t.disease == "ad_non_cholera")
    assert "printed_total_differs_from_sum" in ad.flags  # 85,516 printed vs 85,066 summed


def test_wrapped_label_and_ili_is_never_called_influenza():
    p = parse_province_tables(TEXT)
    assert any(c.disease == "chickenpox_varicella" for c in p.counts)
    assert any(c.disease == "ili_syndrome" for c in p.counts)
    assert not any("influenza" in c.disease for c in p.counts)


def test_missing_title_year_is_rejected_not_guessed():
    body = "\n".join(TEXT.splitlines()[2:])
    p = parse_province_tables(body)
    assert p.counts == [] and p.rejected == [(26, "year_unresolved")]


def test_hyphenated_caption_variant_is_accepted():
    p = parse_province_tables(TEXT.replace("Province/Area wise", "Province/Area-wise"))
    assert len(p.counts) == 23 * 7


def test_absent_disease_row_is_not_reported_as_zero():
    p = parse_province_tables(TEXT.replace("   Dengue ", "   Xdengue "))
    assert not any(c.disease == "dengue" for c in p.counts)  # no row => no value, not 0
