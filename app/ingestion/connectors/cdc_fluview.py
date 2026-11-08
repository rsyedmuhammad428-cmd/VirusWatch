"""CDC FluView connector (ILINet + WHO/NREVSS clinical and public-health lab reports).

Why this reads local files rather than calling a live API: CDC's own FluView Interactive
dashboard (the source of the "Download Data" button that produces exactly the three CSVs this
connector parses) is backed by an internal endpoint on gis.cdc.gov
(`POST /grasp/flu2/PostPhase02DataDownload`, confirmed to exist via the actively-maintained
open-source `cdcfluview` R package's source, https://github.com/hrbrmstr/cdcfluview -- it is not
an invented endpoint). But that endpoint is POST-only with a JSON request body whose parameter
values for selecting "ILINet" vs the two NREVSS lab-type downloads were not independently
confirmed from this sandbox (no network egress to cdc.gov here, and this codebase's shared
`HttpFetcher` port is GET-only -- extending it for one unverified POST body would risk baking in
a guess). Per section 5 of the spec ("Do not assume a stable API exists... Do not invent API
endpoints"), this connector instead ingests the three CSVs from local files -- the same files the
project owner already supplied, saved to disk by a human using CDC's own "Download Data" feature
at https://www.cdc.gov/fluview/surveillance/index.html -- exactly like the NIH bulletin parser
ingests a human-downloaded PDF rather than a guessed API. If CDC later documents a public, GET-
based bulk-download URL for these files, `fetch()` is the only method that needs to change; parsing
and persistence do not.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.connectors.base import SourceConnector
from app.ingestion.connectors.cdc_fluview_parser import (
    PARSER_VERSION,
    parse_ilinet_csv,
    parse_nrevss_clinical_csv,
    parse_nrevss_public_health_csv,
)
from app.ingestion.errors import FetchError, SchemaMismatchError
from app.ingestion.http import HttpFetcher
from app.ingestion.models import IngestRequest, ParseResult, RowRejection, Snapshot

DOWNLOAD_PAGE = "https://www.cdc.gov/fluview/surveillance/index.html"

ILINET_ENTRY = "ILINet.csv"
NREVSS_CLINICAL_ENTRY = "ICL_NREVSS_Clinical_Labs.csv"
NREVSS_PUBLIC_HEALTH_ENTRY = "ICL_NREVSS_Public_Health_Labs.csv"
_ENTRIES = (ILINET_ENTRY, NREVSS_CLINICAL_ENTRY, NREVSS_PUBLIC_HEALTH_ENTRY)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def bundle_bytes(files: dict[str, bytes]) -> bytes:
    """Zip the three raw CSVs into one deterministic archive (stable member order and fixed
    timestamps so identical inputs always hash identically for change detection)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _ENTRIES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zf.writestr(info, files[name])
    return buf.getvalue()


class CdcFluViewConnector(SourceConnector):
    slug = "cdc_fluview"
    parser_version = PARSER_VERSION

    def __init__(
        self,
        *,
        data_dir: Path | str = "data/cdc_fluview",
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._clock = clock

    @property
    def source_url(self) -> str:
        return DOWNLOAD_PAGE

    def query_parameters(self, request: IngestRequest) -> dict[str, object]:
        # CDC FluView publishes one current national release; there is nothing to filter by
        # country/year client-side (the files already cover the full history CDC retains).
        return {"source": "cdc_fluview_local_bundle", "files": list(_ENTRIES)}

    async def fetch(self, request: IngestRequest, fetcher: HttpFetcher) -> Snapshot:
        # `fetcher` is unused: see module docstring for why this reads local files rather than
        # calling a live endpoint.
        missing = [name for name in _ENTRIES if not (self._data_dir / name).is_file()]
        if missing:
            raise FetchError(
                f"Missing local CDC FluView file(s) in {self._data_dir}: {', '.join(missing)}. "
                f"Download them from {DOWNLOAD_PAGE} ('Download Data') and place them there."
            )
        files = {name: (self._data_dir / name).read_bytes() for name in _ENTRIES}
        content = bundle_bytes(files)
        return Snapshot(
            url=self.source_url,
            content=content,
            checksum="sha256:" + hashlib.sha256(content).hexdigest(),
            retrieved_at=self._clock(),
            content_type="application/zip",
            source_published_at=None,  # CDC's own publication timestamp is not in the CSVs
        )

    def parse(self, snapshot: Snapshot, request: IngestRequest) -> ParseResult:
        try:
            with zipfile.ZipFile(io.BytesIO(snapshot.content)) as zf:
                names = set(zf.namelist())
                missing = [n for n in _ENTRIES if n not in names]
                if missing:
                    raise SchemaMismatchError(
                        f"Snapshot bundle is missing entr(y/ies): {', '.join(missing)}."
                    )
                ilinet_bytes = zf.read(ILINET_ENTRY)
                clinical_bytes = zf.read(NREVSS_CLINICAL_ENTRY)
                public_health_bytes = zf.read(NREVSS_PUBLIC_HEALTH_ENTRY)
        except zipfile.BadZipFile as exc:
            raise SchemaMismatchError("Snapshot is not a valid zip bundle.") from exc

        if not request.countries:
            raise ValueError("countries must be non-empty")
        ilinet = parse_ilinet_csv(
            ilinet_bytes, source_url=f"{DOWNLOAD_PAGE}#{ILINET_ENTRY}", countries=request.countries
        )
        clinical = parse_nrevss_clinical_csv(
            clinical_bytes,
            source_url=f"{DOWNLOAD_PAGE}#{NREVSS_CLINICAL_ENTRY}",
            countries=request.countries,
        )
        public_health = parse_nrevss_public_health_csv(
            public_health_bytes,
            source_url=f"{DOWNLOAD_PAGE}#{NREVSS_PUBLIC_HEALTH_ENTRY}",
            countries=request.countries,
        )

        merged = ParseResult(columns=ilinet.columns + clinical.columns + public_health.columns)
        for name, part in (
            (ILINET_ENTRY, ilinet),
            (NREVSS_CLINICAL_ENTRY, clinical),
            (NREVSS_PUBLIC_HEALTH_ENTRY, public_health),
        ):
            merged.observations.extend(part.observations)
            merged.rejections.extend(
                RowRejection(r.row_number, r.reason, f"[{name}] {r.detail}")
                for r in part.rejections
            )
            merged.rows_read += part.rows_read
            merged.rows_out_of_scope += part.rows_out_of_scope
            merged.rows_duplicate_identical += part.rows_duplicate_identical
            merged.warnings.extend(f"[{name}] {w}" for w in part.warnings)
        return merged
