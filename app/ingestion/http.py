"""Fetching with timeouts, bounded retries, exponential backoff and response validation.

The transport sits behind the `HttpFetcher` port so the retry and validation rules can be tested
without a network. The production adapter is app.ingestion.httpx_fetcher.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol

from app.ingestion.errors import FetchError, SnapshotValidationError, TransientFetchError
from app.ingestion.models import Snapshot

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]  # lower-case header names
    body: bytes
    url: str  # final URL


class HttpFetcher(Protocol):
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None,
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResult:
        """One GET. Must raise TransientFetchError for network errors/timeouts and FetchError when
        the body exceeds max_bytes. Must NOT raise for HTTP error statuses; return them instead."""
        ...


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4  # total attempts, including the first
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: float = 0.25  # +/- fraction of the delay

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0 or self.max_delay < self.base_delay or not 0 <= self.jitter < 1:
            raise ValueError("invalid backoff parameters")

    def delay_for(
        self, attempt: int, *, retry_after: float | None, rng: Callable[[], float]
    ) -> float:
        """Seconds to wait after failed attempt number `attempt` (1-based)."""
        backoff = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        backoff *= 1 + self.jitter * (2 * rng() - 1)
        if retry_after is not None:
            # Honour the server's request, but never wait longer than our own ceiling.
            backoff = max(backoff, min(retry_after, self.max_delay))
        return max(0.0, backoff)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Retry-After is either delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


async def fetch_with_retries(
    fetcher: HttpFetcher,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    policy: RetryPolicy = RetryPolicy(),  # noqa: B008 (frozen dataclass: safe as a default)
    max_bytes: int,
    timeout_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: Callable[[], float] = random.random,  # noqa: S311 (jitter, not security)
) -> HttpResult:
    """GET with bounded retries. Returns only a 2xx response; raises FetchError otherwise."""
    last_error: FetchError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        retry_after: float | None = None
        try:
            result = await fetcher.get(
                url, params=params, max_bytes=max_bytes, timeout_seconds=timeout_seconds
            )
        except TransientFetchError as exc:
            last_error, retry_after = exc, exc.retry_after
        else:
            if 200 <= result.status < 300:
                return result
            if result.status in RETRYABLE_STATUS:
                retry_after = parse_retry_after(result.headers.get("retry-after"))
                last_error = TransientFetchError(
                    f"HTTP {result.status} from source", retry_after=retry_after
                )
            else:
                # Permanent: retrying a 400/401/403/404 only hammers the source.
                raise FetchError(f"HTTP {result.status} from source")

        if attempt == policy.max_attempts:
            break
        delay = policy.delay_for(attempt, retry_after=retry_after, rng=rng)
        log.warning(
            "fetch_retry",
            extra={
                "attempt": attempt,
                "max_attempts": policy.max_attempts,
                "delay_s": round(delay, 2),
            },
        )
        await sleep(delay)

    assert last_error is not None
    raise FetchError(
        f"Giving up after {policy.max_attempts} attempts: {last_error}"
    ) from last_error


_REJECTED_CONTENT_TYPES = ("text/html", "application/json", "application/xml", "text/xml")


def build_snapshot(
    result: HttpResult, *, url: str, now: datetime, require_text: bool = True
) -> Snapshot:
    """Validate a 2xx response as a data file and wrap it with checksum + provenance."""
    if not result.body:
        raise SnapshotValidationError("Source returned an empty body.")

    content_type = (result.headers.get("content-type") or "").split(";")[0].strip().lower() or None
    if require_text and content_type and content_type.startswith(_REJECTED_CONTENT_TYPES):
        # Typical failure mode: a 200 OK HTML/JSON error or login page instead of the data file.
        raise SnapshotValidationError(f"Unexpected content type {content_type!r}; expected CSV.")

    declared = result.headers.get("content-length")
    if declared and declared.isdigit() and "content-encoding" not in result.headers:
        # (With content-encoding the declared length is the compressed size, so we cannot compare.)
        if int(declared) != len(result.body):
            raise SnapshotValidationError(
                f"Truncated download: Content-Length {declared}, received {len(result.body)} bytes."
            )

    published = None
    if last_modified := result.headers.get("last-modified"):
        try:
            published = parsedate_to_datetime(last_modified)
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            published = None

    return Snapshot(
        url=url,
        content=result.body,
        checksum="sha256:" + hashlib.sha256(result.body).hexdigest(),
        retrieved_at=now,
        content_type=content_type,
        source_published_at=published,
    )
