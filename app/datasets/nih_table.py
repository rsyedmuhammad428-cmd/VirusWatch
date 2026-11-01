"""Extract Table 1 (province x disease, suspected cases) from the text of NIH IDSR bulletins.

Input is the text produced by `pdftotext -layout`; no PDF library is involved and the PDF is never
altered. Facts the parser enforces:

  * "NR" (not reported) is a missing value, never 0.
  * The bulletin's "Total" column sums only provinces that reported. It is NOT a national figure
    whenever any province is NR, and the row says so.
  * Counts are *suspected* cases from IDSR reporting with no denominator; they are not lab results
    and must never be combined with WHO FluNet positivity, CDC rates or wastewater values.
  * Only the province-level table is parsed. Sindh/Karachi district tables list a different,
    changing set of diseases and are not parsed here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

TITLE = re.compile(r"Public Health Bulletin\s*-\s*Pakistan,\s*Week\s*(\d{1,2}),\s*(20\d\d)")
CAPTION = re.compile(
    r"Table\s*1:\s*Province/Area[\s-]*wise distribution of most frequently reported "
    r"suspected cases "
    r"during Week\s*(\d{1,2}),\s*Pakistan",
    re.IGNORECASE,
)
_CELL = r"(?:\d[\d,]*|NR|-)"
_ROW = re.compile(rf"^\s*(?P<label>\S.*?)\s{{2,}}(?P<values>{_CELL}(?:\s+{_CELL})+)\s*$")
_HEADER = re.compile(r"^\s*Diseases\s+(?P<cols>(?:[A-Za-z]+\s*)+)$")

# Slugs for the diseases VirusWatch prioritises. ILI/SARI are syndromes, never influenza counts.
DISEASE_SLUGS = {
    "dengue": "dengue",
    "malaria": "malaria",
    "measles": "measles",
    "ili": "ili_syndrome",
    "sari": "sari_syndrome",
}


@dataclass(frozen=True)
class ProvinceCount:
    year: int
    week: int
    disease_label: str
    disease: str  # snake_case slug
    province: str  # as printed: AJK, Balochistan, GB, ICT, KP, Punjab, Sindh
    value: Decimal | None  # None = NR or '-'
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class BulletinTotal:
    year: int
    week: int
    disease: str
    printed_total: Decimal | None
    sum_of_reporting: Decimal | None
    reporting_provinces: tuple[str, ...]
    non_reporting_provinces: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass
class TableParse:
    counts: list[ProvinceCount] = field(default_factory=list)
    totals: list[BulletinTotal] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)  # (week, reason)


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _number(token: str) -> Decimal | None:
    return None if token in {"NR", "-"} else Decimal(token.replace(",", ""))


def _year_for(text: str, position: int, week: int) -> int | None:
    """Year of the bulletin whose title most recently precedes this table and names this week."""
    year = None
    for m in TITLE.finditer(text, 0, position):
        if int(m.group(1)) == week:
            year = int(m.group(2))
    return year


def parse_province_tables(text: str) -> TableParse:
    """Parse every 'Table 1' found in the text (one bulletin, or a concatenation of many)."""
    result = TableParse()
    lines = text.splitlines()
    offsets = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    for index, line in enumerate(lines):
        caption = CAPTION.search(line)
        if not caption:
            continue
        week = int(caption.group(1))
        year = _year_for(text, offsets[index], week)
        if year is None:
            result.rejected.append((week, "year_unresolved"))
            continue
        _parse_one(lines, index + 1, year, week, result)
    return result


def _parse_one(lines: list[str], start: int, year: int, week: int, out: TableParse) -> None:
    columns: list[str] | None = None
    pending_label = ""
    blank_run = 0
    index = start
    rows: list[tuple[str, list[str]]] = []
    while index < len(lines) and index < start + 60:
        line = lines[index]
        index += 1
        if columns is None:
            header = _HEADER.match(line)
            if header:
                columns = header.group("cols").split()
            continue
        if not line.strip():
            blank_run += 1
            if blank_run > 2 and rows:
                break
            continue
        blank_run = 0
        row = _ROW.match(line)
        if row and len(row.group("values").split()) == len(columns):
            label = (
                (pending_label + " " + row.group("label")).strip()
                if pending_label
                else (row.group("label").strip())
            )
            pending_label = ""
            rows.append((label, row.group("values").split()))
        elif re.search(r"[A-Za-z]/\s*$", line):  # wrapped label, e.g. "Chickenpox/"
            pending_label = line.strip()
        elif rows:
            break
    if columns is None or not rows or columns[-1] != "Total":
        out.rejected.append((week, "table_not_found_or_unexpected_header"))
        return
    provinces = columns[:-1]
    for label, tokens in rows:
        disease = DISEASE_SLUGS.get(label.lower(), slugify(label))
        reporting: list[str] = []
        silent: list[str] = []
        total_sum = Decimal(0)
        for province, token in zip(provinces, tokens[:-1], strict=True):
            value = _number(token)
            flags = ("not_reported",) if value is None else ()
            out.counts.append(ProvinceCount(year, week, label, disease, province, value, flags))
            if value is None:
                silent.append(province)
            else:
                reporting.append(province)
                total_sum += value
        printed = _number(tokens[-1])
        flags = []
        if silent:
            flags.append("total_excludes_non_reporting")
        if printed is not None and reporting and printed != total_sum:
            flags.append("printed_total_differs_from_sum")
        out.totals.append(
            BulletinTotal(
                year,
                week,
                disease,
                printed,
                total_sum if reporting else None,
                tuple(reporting),
                tuple(silent),
                tuple(flags),
            )
        )
