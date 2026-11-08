from __future__ import annotations

import csv
import io

ILINET_PREAMBLE = "PERCENTAGE OF VISITS FOR INFLUENZA-LIKE-ILLNESS REPORTED BY SENTINEL PROVIDERS"
NREVSS_PREAMBLE = (
    '"*Beginning for the 2015-16 season, reports from public health and clinical laboratories '
    'are presented separately in this table."'
)

ILINET_HEADER = [
    "REGION TYPE",
    "REGION",
    "YEAR",
    "WEEK",
    "% WEIGHTED ILI",
    "%UNWEIGHTED ILI",
    "AGE 0-4",
    "AGE 25-49",
    "AGE 25-64",
    "AGE 5-24",
    "AGE 50-64",
    "AGE 65",
    "ILITOTAL",
    "NUM. OF PROVIDERS",
    "TOTAL PATIENTS",
]

NREVSS_CLINICAL_HEADER = [
    "REGION TYPE",
    "REGION",
    "YEAR",
    "WEEK",
    "TOTAL SPECIMENS",
    "TOTAL A",
    "TOTAL B",
    "PERCENT POSITIVE",
    "PERCENT A",
    "PERCENT B",
]

NREVSS_PUBLIC_HEALTH_HEADER = [
    "REGION TYPE",
    "REGION",
    "YEAR",
    "WEEK",
    "TOTAL SPECIMENS",
    "A (2009 H1N1)",
    "A (H3)",
    "A (Subtyping not Performed)",
    "B",
    "BVic",
    "BYam",
    "H3N2v",
    "A (H5)",
]


def csv_bytes(preamble: str, header: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    buf.write(preamble + "\n")
    writer = csv.DictWriter(buf, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in header})
    return buf.getvalue().encode("utf-8")


def ilinet_row(**overrides: str) -> dict[str, str]:
    base = {
        "REGION TYPE": "National",
        "REGION": "X",
        "YEAR": "2023",
        "WEEK": "40",
        "% WEIGHTED ILI": "2.27908",
        "%UNWEIGHTED ILI": "2.26141",
        "AGE 0-4": "5000",
        "AGE 25-49": "15000",
        "AGE 25-64": "X",
        "AGE 5-24": "20000",
        "AGE 50-64": "8000",
        "AGE 65": "4000",
        "ILITOTAL": "56309",
        "NUM. OF PROVIDERS": "2884",
        "TOTAL PATIENTS": "2489991",
    }
    base.update(overrides)
    return base


def ilinet_csv_bytes(rows: list[dict[str, str]], *, preamble: str = ILINET_PREAMBLE) -> bytes:
    return csv_bytes(preamble, ILINET_HEADER, rows)


def nrevss_clinical_row(**overrides: str) -> dict[str, str]:
    base = {
        "REGION TYPE": "National",
        "REGION": "X",
        "YEAR": "2023",
        "WEEK": "40",
        "TOTAL SPECIMENS": "64939",
        "TOTAL A": "400",
        "TOTAL B": "315",
        "PERCENT POSITIVE": "1.1010",
        "PERCENT A": "0.6160",
        "PERCENT B": "0.4850",
    }
    base.update(overrides)
    return base


def nrevss_clinical_csv_bytes(
    rows: list[dict[str, str]], *, preamble: str = NREVSS_PREAMBLE
) -> bytes:
    return csv_bytes(preamble, NREVSS_CLINICAL_HEADER, rows)


def nrevss_public_health_row(**overrides: str) -> dict[str, str]:
    base = {
        "REGION TYPE": "National",
        "REGION": "X",
        "YEAR": "2023",
        "WEEK": "40",
        "TOTAL SPECIMENS": "3064",
        "A (2009 H1N1)": "176",
        "A (H3)": "16",
        "A (Subtyping not Performed)": "10",
        "B": "3",
        "BVic": "48",
        "BYam": "0",
        "H3N2v": "0",
        "A (H5)": "0",
    }
    base.update(overrides)
    return base


def nrevss_public_health_csv_bytes(
    rows: list[dict[str, str]], *, preamble: str = NREVSS_PREAMBLE
) -> bytes:
    return csv_bytes(preamble, NREVSS_PUBLIC_HEALTH_HEADER, rows)
