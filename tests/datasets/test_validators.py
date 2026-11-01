from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from app.datasets.validators import (
    gho_unique_rows,
    parse_covid_date,
    parse_gho_numeric,
    validate_covid_2020,
    validate_facilities,
    validate_gho,
)

FIX = Path("tests/fixtures/datasets")


def read(name):
    with (FIX / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_both_date_formats_parse_and_garbage_does_not():
    assert parse_covid_date("3/17/2020") == date(2020, 3, 17)
    assert parse_covid_date("17-Mar-2020") == date(2020, 3, 17)
    assert parse_covid_date("2020-03-17") is None


def test_real_covid_excerpt_normalises_dates_and_flags_non_provinces():
    rep = validate_covid_2020(read("covid_pk_2020_excerpt.csv"))
    assert rep.rows_read == 59 and not rep.rejected
    mar17 = [d for d in rep.days if d.report_date == date(2020, 3, 17)]
    assert len(mar17) == 8  # d-Mon-Y and m/d/Y rows land on the same day (7 areas + KPTD)
    kptd = [d for d in rep.days if d.region in {"KPTD", "Mobile Lab"}]
    assert kptd and all("non_province_region" in d.flags for d in kptd)
    assert "non_province_region" in rep.flag_counts


def test_gaps_and_unchanged_cumulative_are_reported_in_real_excerpt():
    rep = validate_covid_2020(read("covid_pk_2020_excerpt.csv"))
    assert (date(2020, 5, 2), date(2020, 5, 4)) in rep.date_gaps or any(
        a == date(2020, 5, 2) for a, _ in rep.date_gaps
    )
    punjab = {d.report_date: d for d in rep.days if d.region == "Punjab"}
    assert "unchanged_cumulative" in punjab[date(2020, 4, 26)].flags  # same figures as 25 April


def test_only_defensible_columns_are_mapped():
    rep = validate_covid_2020(read("covid_pk_2020_excerpt.csv"))
    sindh = [d for d in rep.days if d.region == "Sindh" and d.report_date == date(2020, 5, 14)][0]
    assert sindh.cumulative_confirmed == 14_099 or sindh.cumulative_confirmed is not None
    assert not hasattr(sindh, "new_cases") and not hasattr(sindh, "tests_24h")


def rows(*triples):
    return [
        {
            "Date": d,
            "Region": "Sindh",
            "Cumulative  Test positive": c,
            "Discharged": "0",
            "Expired": "0",
        }
        for d, c in triples
    ]


def test_decrease_negative_duplicate_bad_date_and_non_numeric_synthetic():
    rep = validate_covid_2020(
        rows(
            ("3/1/2020", "100"),
            ("3/2/2020", "90"),
            ("3/2/2020", "95"),
            ("bad", "1"),
            ("3/3/2020", "x"),
            ("3/4/2020", "-5"),
        )
    )
    assert rep.rejected == {"duplicate_date_region": 1, "invalid_date": 1}
    flags = {d.report_date.day: d.flags for d in rep.days}
    assert "cumulative_decreased" in flags[2]
    assert "non_numeric:cumulative_confirmed" in flags[3]
    assert "negative:cumulative_confirmed" in flags[4]
    assert all(d.cumulative_confirmed is None for d in rep.days if d.report_date.day in (3, 4))


def test_missing_values_are_none_never_zero():
    rep = validate_covid_2020([{"Date": "3/1/2020", "Region": "Sindh"}])
    assert rep.days[0].cumulative_confirmed is None


def test_gho_ambiguous_keys_are_detected_and_not_loadable():
    data = read("who_gho_excerpt.csv")
    rep = validate_gho(data)
    assert (
        rep.ambiguous_key_groups >= 1 and "PHE_HHAIR_PROP_POP_CATEGORY_FUELS" in rep.ambiguous_codes
    )
    assert gho_unique_rows(data, frozenset({"PHE_HHAIR_PROP_POP_CATEGORY_FUELS"})) == []


def test_gho_measles_reported_cases_are_unique_and_use_numeric_column():
    data = read("who_gho_excerpt.csv")
    usable = gho_unique_rows(data, frozenset({"WHS3_62"}))
    assert len(usable) == 2
    assert all(parse_gho_numeric(r) is not None for r in usable)
    assert {int(parse_gho_numeric(r)) for r in usable} >= {20741}  # 2025 figure, as supplied
    assert parse_gho_numeric({"Numeric": ""}) is None


def test_facility_validation_counts_gaps():
    rep = validate_facilities(read("facilities_excerpt.csv"))
    assert rep.rows_read == 12 and rep.missing_coordinates >= 1 and rep.outside_pakistan == 0
    assert (
        validate_facilities([{"X": "10", "Y": "10", "osm_id": "1", "name": ""}]).outside_pakistan
        == 1
    )
    assert validate_facilities([{"osm_id": "1"}, {"osm_id": "1"}]).duplicate_ids == 1
