"""Contract test: the parser reads only columns WHO documents for VIW_FNT, with documented types.

tests/fixtures/flunet/viw_fnt_fields.csv holds just the field names and data types taken from WHO's
VIW_FLU_METADATA export (not its descriptions), retrieved 2026-10-30. It is REAL WHO metadata, not
surveillance data.
"""

from __future__ import annotations

import csv

from app.ingestion.connectors import flunet_parser as fp
from tests.ingestion.flunet_helpers import FIXTURE

FIELDS_FILE = FIXTURE.parent / "viw_fnt_fields.csv"


def documented_fields() -> dict[str, str]:
    with FIELDS_FILE.open(newline="") as fh:
        return {r["FieldName"]: r["DataType"] for r in csv.DictReader(fh)}


def columns_read_by_parser() -> list[str]:
    return [
        *fp.REQUIRED_COLUMNS,
        fp.WEEK_START_COLUMN,
        fp.SPECIMENS_COLUMN,
        *(column for column, _ in fp.PATHOGEN_COLUMNS),
        *fp.VICTORIA_COLUMNS,
    ]


def test_dictionary_has_the_53_documented_fields():
    assert len(documented_fields()) == 53


def test_every_column_the_parser_reads_is_documented():
    missing = [c for c in columns_read_by_parser() if c not in documented_fields()]
    assert not missing, f"parser reads undocumented columns: {missing}"


def test_count_columns_are_documented_as_integers():
    fields = documented_fields()
    count_columns = [
        "INF_ALL",
        "INF_NEGATIVE",
        fp.SPECIMENS_COLUMN,
        *(c for c, _ in fp.PATHOGEN_COLUMNS),
        *fp.VICTORIA_COLUMNS,
    ]
    assert all(fields[c] == "integer" for c in count_columns)


def test_identifier_columns_have_documented_types():
    fields = documented_fields()
    assert fields["ISO_YEAR"] == fields["ISO_WEEK"] == "integer"
    assert fields["ISO_WEEKSTARTDATE"] == "date"
    assert fields["COUNTRY_CODE"] == fields["ORIGIN_SOURCE"] == "string"


def test_stream_values_match_the_documented_set():
    # Dictionary: "Surveillance site type (NONSENTINEL/SENTINEL/NOTDEFINED)"
    assert set(fp.STREAMS) == {"NONSENTINEL", "SENTINEL", "NOTDEFINED"}
