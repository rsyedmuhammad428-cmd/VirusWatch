"""WHO FluNet connector: country-level influenza laboratory surveillance.

Endpoint (published on https://www.who.int/toolkits/flunet): the OData view VIW_FNT with
`$format=csv`. We add `$filter` (countries, ISO year) so we download only what we need. The
CSV is fetched in one request per run; ingestion therefore adds negligible load on WHO.

Unverified from this environment (to confirm on the first real run, see README):
  * that the endpoint honours `$filter` on COUNTRY_CODE / ISO_YEAR for CSV output, and
  * that CSV output is not paginated or truncated server-side.
A rejected filter (HTTP 4xx) or a schema/row-count surprise fails the run loudly instead of
ingesting something partial.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.ingestion.connectors.base import SourceConnector
from app.ingestion.connectors.flunet_parser import PARSER_VERSION, parse_flunet_csv
from app.ingestion.http import HttpFetcher, RetryPolicy, build_snapshot, fetch_with_retries
from app.ingestion.models import IngestRequest, ParseResult, Snapshot

FLUNET_ENDPOINT = "https://xmart-api-public.who.int/FLUMART/VIW_FNT"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FluNetConnector(SourceConnector):
    slug = "who_flunet"
    parser_version = PARSER_VERSION

    def __init__(
        self,
        *,
        endpoint: str = FLUNET_ENDPOINT,
        retry_policy: RetryPolicy | None = None,
        max_bytes: int = 100 * 1024 * 1024,
        timeout_seconds: float = 120.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._endpoint = endpoint
        self._retry = retry_policy or RetryPolicy()
        self._max_bytes = max_bytes
        self._timeout = timeout_seconds
        self._clock = clock

    @property
    def source_url(self) -> str:
        return f"{self._endpoint}?$format=csv"

    @staticmethod
    def odata_filter(request: IngestRequest) -> str:
        # Codes come from a fixed allow-list (resolve_countries), so interpolation is safe. This
        # explicit check (not an assert, which -O strips) keeps it safe if that step is bypassed.
        if not request.countries or not all(
            len(c) == 3 and c.isascii() and c.isalpha() and c.isupper() for c in request.countries
        ):
            raise ValueError("countries must be non-empty upper-case ISO 3166-1 alpha-3 codes")
        clause = " or ".join(f"COUNTRY_CODE eq '{c}'" for c in sorted(request.countries))
        expression = f"({clause})"
        if request.since_year is not None:
            if not 1900 <= request.since_year <= 2100:
                raise ValueError("since_year out of range")
            expression += f" and ISO_YEAR ge {int(request.since_year)}"
        return expression

    def query_parameters(self, request: IngestRequest) -> dict[str, object]:
        return {"endpoint": self._endpoint, "$format": "csv", "$filter": self.odata_filter(request)}

    async def fetch(self, request: IngestRequest, fetcher: HttpFetcher) -> Snapshot:
        params = {k: str(v) for k, v in self.query_parameters(request).items() if k.startswith("$")}
        result = await fetch_with_retries(
            fetcher,
            self._endpoint,
            params=params,
            policy=self._retry,
            max_bytes=self._max_bytes,
            timeout_seconds=self._timeout,
        )
        return build_snapshot(result, url=self.source_url, now=self._clock())

    def parse(self, snapshot: Snapshot, request: IngestRequest) -> ParseResult:
        return parse_flunet_csv(
            snapshot.content,
            countries=request.countries,
            since_year=request.since_year,
            source_url=self.source_url,
        )
