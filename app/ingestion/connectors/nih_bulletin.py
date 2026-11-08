"""Pakistan NIH Weekly Public Health Bulletin connector.

Wraps the pure, already-tested Table 1 extractor (app.datasets.nih_table) so its output reaches
the database through the same runner used by every other connector (dedup by checksum, run
tracking, rejection reporting).

Why this reads a local PDF rather than calling a live API: NIH publishes these bulletins as PDFs
linked from https://www.nih.org.pk/phb/weekly-bulletin, with no documented API, no stable
predictable URL pattern verified here, and no bulk-download feature found. Per section 5 of the
spec ("Do not assume a stable API exists... verify the actual source format before implementing a
connector"), this connector ingests a human-downloaded PDF from local disk, exactly like
app.ingestion.connectors.cdc_fluview does for CDC's FluView CSVs.

Geographic resolution, stated plainly: the bulletin reports Table 1 at province level (AJK,
Balochistan, GB, ICT, KP, Punjab, Sindh), but this project's observation schema
(app.ingestion.models.ObservationRecord) has no sub-national geography field yet -- only
`country_code`. Adding one is a cross-cutting schema change (geographies table, persistence SQL,
RLS policies, the analysis/surveillance API) out of scope for this phase. Rather than silently
dropping the province breakdown or miscounting it as a national total, this connector persists
Pakistan-level ObservationRecords built from BulletinTotal.sum_of_reporting (the sum of PROVINCES
THAT REPORTED, never invented for provinces marked NR), carries the
`total_excludes_non_reporting` flag through to `quality_flags` whenever a province did not report,
and preserves the full per-province breakdown losslessly in `metadata` for when province-level
geography is added. This is documented as a known limitation in docs/PHASE7_REPORT.md, not
presented as full geographic fidelity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.datasets.nih_table import BulletinTotal, parse_province_tables
from app.ingestion.connectors.base import SourceConnector
from app.ingestion.errors import FetchError, SchemaMismatchError
from app.ingestion.http import HttpFetcher
from app.ingestion.models import (
    IngestRequest,
    ObservationRecord,
    ParseResult,
    RowRejection,
    Snapshot,
)
from app.rag.extract import PdfExtractionError, extract_pdf

PARSER_VERSION = "nih-bulletin-1"
BULLETIN_PAGE = "https://www.nih.org.pk/phb/weekly-bulletin"
COUNTRY_CODE = "PK"

# Diseases this project prioritises (Prompt.txt / DATA PROMPT.txt); other diseases in the table
# are still parsed and persisted (indicator_type "other"), just not singled out here.
PRIORITY_DISEASES = {"dengue", "malaria", "measles", "ili_syndrome"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _pdftotext(pdf_bytes: bytes) -> str:
    """Extract plain text via the same, already-tested poppler pdftotext -layout wrapper the RAG
    pipeline uses (app.rag.extract.extract_pdf), reconstructed into one continuous document (one
    page's text per line-run) since app.datasets.nih_table's regexes scan across the whole
    document rather than per-page."""
    try:
        doc = extract_pdf(pdf_bytes)
    except PdfExtractionError as exc:
        raise SchemaMismatchError(f"PDF text extraction failed: {exc}") from exc
    return "\n".join(page.text for page in doc.pages)


def _week_dates(year: int, week: int) -> tuple[date, date]:
    # IDSR bulletins use ISO-style week numbering in their own title line (verified against the
    # real Week 26, 2026 bulletin: Monday-start). Unlike CDC's MMWR weeks, NIH's own title gives
    # us the year directly per table, so no cross-year rollover ambiguity to resolve here.
    start = date.fromisocalendar(year, week, 1)
    return start, start + timedelta(days=6)


def _to_observation(total: BulletinTotal, *, source_url: str, by_province) -> ObservationRecord:
    start, end = _week_dates(total.year, total.week)
    flags = list(total.flags)
    value = total.sum_of_reporting
    data_status = "reported" if value is not None else "missing"
    metadata: dict[str, object] = {
        "disease_label_as_printed": by_province[0].disease_label if by_province else None,
        "reporting_provinces": list(total.reporting_provinces),
        "non_reporting_provinces": list(total.non_reporting_provinces),
        "printed_total": str(total.printed_total) if total.printed_total is not None else None,
        "province_counts": {
            p.province: (str(p.value) if p.value is not None else None) for p in by_province
        },
    }
    return ObservationRecord(
        country_code=COUNTRY_CODE,
        disease=total.disease,
        indicator="suspected_cases_reported",
        # IDSR syndromic/case-based reporting: no lab confirmation, no denominator. Does not fit
        # clinical_surveillance (a rate) or laboratory_* (lab results), so "other" is honest here
        # rather than forcing it into a category it is not (see module docstring and
        # app.datasets.nih_table's own module docstring).
        indicator_type="other",
        surveillance_stream="undefined",
        reporting_period_start=start,
        reporting_period_end=end,
        value_numeric=value,
        numerator=value,
        denominator=None,
        unit="count",
        data_status=data_status,
        quality_flags=tuple(flags),
        source_record_id=f"nih_bulletin:{total.year}-W{total.week:02d}:{total.disease}",
        source_url=source_url,
        metadata=metadata,
    )


class NihBulletinConnector(SourceConnector):
    slug = "pk_nih_weekly_bulletin"
    parser_version = PARSER_VERSION

    def __init__(
        self,
        *,
        data_dir: Path | str = "data/nih_bulletins",
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._clock = clock

    @property
    def source_url(self) -> str:
        return BULLETIN_PAGE

    def query_parameters(self, request: IngestRequest) -> dict[str, object]:
        return {"source": "nih_bulletin_local_pdfs", "data_dir": str(self._data_dir)}

    async def fetch(self, request: IngestRequest, fetcher: HttpFetcher) -> Snapshot:
        # `fetcher` is unused: see module docstring for why this reads local PDFs rather than
        # calling a live endpoint.
        if not self._data_dir.is_dir():
            raise FetchError(
                f"{self._data_dir} does not exist. Download bulletin PDF(s) from "
                f"{BULLETIN_PAGE} and place them there."
            )
        pdfs = sorted(self._data_dir.glob("*.pdf"))
        if not pdfs:
            raise FetchError(
                f"No PDF files found in {self._data_dir}. Download bulletin PDF(s) from "
                f"{BULLETIN_PAGE} and place them there."
            )
        # Bundle every PDF's bytes, length-prefixed, into one deterministic blob so the snapshot
        # checksum changes exactly when the set of bulletins on disk changes.
        parts = []
        for path in pdfs:
            data = path.read_bytes()
            parts.append(len(data).to_bytes(8, "big") + path.name.encode() + b"\x00" + data)
        content = b"".join(parts)
        return Snapshot(
            url=self.source_url,
            content=content,
            checksum="sha256:" + hashlib.sha256(content).hexdigest(),
            retrieved_at=self._clock(),
            content_type="application/octet-stream",
            source_published_at=None,
        )

    def parse(self, snapshot: Snapshot, request: IngestRequest) -> ParseResult:
        pdfs: list[tuple[str, bytes]] = []
        offset = 0
        content = snapshot.content
        while offset < len(content):
            length = int.from_bytes(content[offset : offset + 8], "big")
            offset += 8
            nul = content.index(b"\x00", offset)
            name = content[offset:nul].decode()
            offset = nul + 1
            data = content[offset : offset + length]
            offset += length
            pdfs.append((name, data))

        result = ParseResult(columns=())
        for name, data in pdfs:
            try:
                text = _pdftotext(data)
            except SchemaMismatchError as exc:
                result.warnings.append(f"[{name}] text extraction failed: {exc}")
                continue
            parse = parse_province_tables(text)
            result.rows_read += len(parse.totals)
            for week, reason in parse.rejected:
                result.rejections.append(
                    RowRejection(
                        week, reason, f"[{name}] Table 1 for week {week} not found/parseable"
                    )
                )
            by_key: dict[tuple[int, int, str], list] = {}
            for count in parse.counts:
                by_key.setdefault((count.year, count.week, count.disease), []).append(count)
            for total in parse.totals:
                by_province = by_key.get((total.year, total.week, total.disease), [])
                result.observations.append(
                    _to_observation(
                        total,
                        source_url=f"{BULLETIN_PAGE}#{name}",
                        by_province=by_province,
                    )
                )
        if not result.observations and not result.rejections:
            raise SchemaMismatchError(
                "No Table 1 (province-wise suspected cases) found in any supplied PDF. This "
                "connector only parses that table; narrative bulletins (e.g. seasonal alert "
                "issues) belong in the RAG document pipeline (app.rag), not here."
            )
        return result
