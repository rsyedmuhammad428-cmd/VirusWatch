"""Builders for FluNet CSV test data. All data produced here is synthetic."""

from __future__ import annotations

from pathlib import Path

FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "flunet" / "SYNTHETIC_FIXTURE_not_real_who_data.csv"
)

HEADER = (
    "COUNTRY_CODE,ISO_YEAR,ISO_WEEK,ISO_WEEKSTARTDATE,ORIGIN_SOURCE,SPEC_PROCESSED_NB,"
    "AH1N12009,AH3,INF_A,BVIC_2DEL,BVIC_3DEL,BVIC_NODEL,BVIC_DELUNK,BYAM,INF_B,INF_ALL,INF_NEGATIVE"
)
COLUMNS = HEADER.split(",")


def row(**values: object) -> str:
    """One CSV line. Unspecified columns are blank; defaults describe a valid PAK 2024-W01 row."""
    defaults = {
        "COUNTRY_CODE": "PAK",
        "ISO_YEAR": 2024,
        "ISO_WEEK": 1,
        "ISO_WEEKSTARTDATE": "2024-01-01",
        "ORIGIN_SOURCE": "SENTINEL",
        "INF_ALL": 5,
        "INF_NEGATIVE": 45,
    }
    merged = {**defaults, **values}
    unknown = set(merged) - set(COLUMNS)
    assert not unknown, f"unknown columns {unknown}"
    return ",".join("" if merged.get(c) is None else str(merged.get(c, "")) for c in COLUMNS)


def csv_bytes(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")
