"""The real Downloader (app.rag.ports.Downloader) used by `python -m app.cli ingest-documents`.

Import-guarded: `httpx` cannot be installed in this sandbox (no PyPI access -- see
requirements.txt), so this has never made a real HTTP request here. app/rag/pipeline.py is
tested against a fake downloader instead (tests/rag/test_pipeline.py).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.rag.models import RawDocument, SourceSpec


class DownloadError(RuntimeError):
    pass


def _import_httpx():
    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as exc:
        raise DownloadError(
            "httpx is not installed in this environment (see requirements.txt and "
            "docs/PHASE5_REPORT.md)."
        ) from exc
    return httpx


class HttpxDownloader:
    def __init__(self, *, user_agent: str, timeout_seconds: float = 30.0) -> None:
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds

    async def fetch(self, source: SourceSpec) -> RawDocument:
        httpx = _import_httpx()
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds, headers={"User-Agent": self._user_agent}
        ) as client:
            try:
                response = await client.get(source.url, follow_redirects=True)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - any network failure becomes DownloadError
                raise DownloadError(f"fetching {source.url} failed: {exc}") from exc
        content = response.content
        content_type = "application/pdf" if source.url.lower().endswith(".pdf") else "text/html"
        return RawDocument(
            content=content,
            content_type=content_type,  # type: ignore[arg-type]
            checksum=f"sha256:{hashlib.sha256(content).hexdigest()}",
            retrieved_at=datetime.now(UTC),
        )
