"""The contract every data-source connector implements (section 6 of the spec).

A connector knows how to *fetch* a snapshot and *parse* it into canonical observations. It never
touches the database: persistence, run bookkeeping, change detection and dry-run live in
app.ingestion.runner so every connector gets identical, tested behaviour.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod

from app.ingestion.http import HttpFetcher
from app.ingestion.models import IngestRequest, ParseResult, Snapshot


class SourceConnector(ABC):
    #: matches data_sources.slug
    slug: str
    #: bump whenever parsing/normalisation rules change; it is part of the snapshot signature so
    #: a rule change re-ingests even when the downloaded bytes are identical.
    parser_version: str

    @abstractmethod
    def query_parameters(self, request: IngestRequest) -> dict[str, object]:
        """Everything that determines *what* is downloaded (endpoint, filters)."""

    @abstractmethod
    async def fetch(self, request: IngestRequest, fetcher: HttpFetcher) -> Snapshot:
        """Download the latest data. Raises FetchError subclasses on failure."""

    @abstractmethod
    def parse(self, snapshot: Snapshot, request: IngestRequest) -> ParseResult:
        """Pure function of (bytes, request). Raises SchemaMismatchError if the file is unusable."""

    def signature(self, request: IngestRequest) -> str:
        """Stable fingerprint of (query, parser version). Same checksum + same signature means a
        re-ingest could not change anything."""
        payload = json.dumps(
            {"q": self.query_parameters(request), "parser": self.parser_version},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
