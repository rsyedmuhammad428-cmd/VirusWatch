from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.errors import FetchError, SnapshotValidationError, TransientFetchError
from app.ingestion.http import (
    HttpResult,
    RetryPolicy,
    build_snapshot,
    fetch_with_retries,
    parse_retry_after,
)
from tests.ingestion.fakes import FakeFetcher, ok, status

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


DEFAULT_POLICY = RetryPolicy(max_attempts=4, base_delay=1, max_delay=30, jitter=0.0)


def fetch(fetcher, policy=DEFAULT_POLICY, sleeps=None):
    sleeps = sleeps or Sleeps()
    result = run(
        fetch_with_retries(
            fetcher,
            "https://example.test/x",
            policy=policy,
            max_bytes=1000,
            timeout_seconds=5,
            sleep=sleeps,
            rng=lambda: 0.5,
        )
    )
    return result, sleeps


# ---------------------------------------------------------------- RetryPolicy
class TestRetryPolicy:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_attempts": 0},
            {"base_delay": -1},
            {"base_delay": 5, "max_delay": 1},
            {"jitter": 1.0},
        ],
    )
    def test_invalid_parameters_rejected(self, kwargs):
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)

    def test_exponential_growth_capped_at_max(self):
        p = RetryPolicy(base_delay=1, max_delay=5, jitter=0.0)
        assert [p.delay_for(n, retry_after=None, rng=lambda: 0.5) for n in (1, 2, 3, 4, 5)] == [
            1,
            2,
            4,
            5,
            5,
        ]

    def test_jitter_stays_within_bounds(self):
        p = RetryPolicy(base_delay=10, max_delay=100, jitter=0.25)
        assert p.delay_for(1, retry_after=None, rng=lambda: 0.0) == pytest.approx(7.5)
        assert p.delay_for(1, retry_after=None, rng=lambda: 1.0) == pytest.approx(12.5)

    def test_retry_after_is_honoured_but_capped(self):
        p = RetryPolicy(base_delay=1, max_delay=30, jitter=0.0)
        assert p.delay_for(1, retry_after=10, rng=lambda: 0.5) == 10
        assert p.delay_for(1, retry_after=9999, rng=lambda: 0.5) == 30
        assert p.delay_for(3, retry_after=1, rng=lambda: 0.5) == 4  # backoff still wins when larger


class TestRetryAfterParsing:
    def test_seconds(self):
        assert parse_retry_after("12") == 12.0

    def test_http_date_relative_to_now(self):
        assert parse_retry_after("Fri, 30 Oct 2026 12:01:00 GMT", now=NOW) == 60.0

    def test_past_date_is_zero(self):
        assert parse_retry_after("Fri, 30 Oct 2026 11:00:00 GMT", now=NOW) == 0.0

    @pytest.mark.parametrize("raw", [None, "", "soon", "-5"])
    def test_garbage_is_ignored(self, raw):
        assert parse_retry_after(raw, now=NOW) is None


# ---------------------------------------------------------------- fetch_with_retries
class TestFetchWithRetries:
    def test_success_on_first_attempt_makes_one_call(self):
        fetcher = FakeFetcher(ok(b"data"))
        result, sleeps = fetch(fetcher)
        assert result.body == b"data" and len(fetcher.calls) == 1 and sleeps.delays == []

    def test_retries_5xx_then_succeeds_with_backoff(self):
        fetcher = FakeFetcher(status(503), status(502), ok(b"data"))
        result, sleeps = fetch(fetcher)
        assert result.body == b"data"
        assert sleeps.delays == [1, 2]

    def test_retry_after_header_respected_for_429(self):
        fetcher = FakeFetcher(status(429, **{"retry-after": "7"}), ok(b"data"))
        _, sleeps = fetch(fetcher)
        assert sleeps.delays == [7]

    def test_transient_network_errors_are_retried(self):
        fetcher = FakeFetcher(TransientFetchError("Timed out"), ok(b"data"))
        result, _ = fetch(fetcher)
        assert result.body == b"data" and len(fetcher.calls) == 2

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 410])
    def test_client_errors_are_permanent_and_not_retried(self, code):
        fetcher = FakeFetcher(status(code))
        with pytest.raises(FetchError, match=f"HTTP {code}") as exc:
            fetch(fetcher)
        assert not isinstance(exc.value, TransientFetchError)
        assert len(fetcher.calls) == 1

    def test_gives_up_after_max_attempts(self):
        fetcher = FakeFetcher(status(503))
        with pytest.raises(FetchError, match="Giving up after 3 attempts"):
            fetch(fetcher, policy=RetryPolicy(max_attempts=3, base_delay=0, max_delay=0))
        assert len(fetcher.calls) == 3

    def test_no_sleep_after_the_final_failed_attempt(self):
        sleeps = Sleeps()
        with pytest.raises(FetchError):
            fetch(
                FakeFetcher(status(500)),
                RetryPolicy(max_attempts=3, base_delay=1, jitter=0),
                sleeps,
            )
        assert len(sleeps.delays) == 2

    def test_non_transient_fetch_error_from_transport_is_not_retried(self):
        fetcher = FakeFetcher(FetchError("Download exceeded the limit"))
        with pytest.raises(FetchError, match="exceeded"):
            fetch(fetcher)
        assert len(fetcher.calls) == 1

    def test_params_and_limits_are_passed_through(self):
        fetcher = FakeFetcher(ok(b"x"))
        run(
            fetch_with_retries(
                fetcher,
                "https://example.test/x",
                params={"$format": "csv"},
                max_bytes=123,
                timeout_seconds=9,
            )
        )
        assert (
            fetcher.calls[0]["params"] == {"$format": "csv"}
            and fetcher.calls[0]["max_bytes"] == 123
        )


# ---------------------------------------------------------------- build_snapshot
def snap(body=b"a,b\n1,2\n", **headers):
    result = HttpResult(
        200, {"content-type": "text/csv", **headers}, body, "https://example.test/x"
    )
    return build_snapshot(result, url="https://example.test/x", now=NOW)


class TestBuildSnapshot:
    def test_checksum_is_sha256_of_the_exact_bytes(self):
        s = snap(b"abc")
        assert s.checksum == "sha256:" + hashlib.sha256(b"abc").hexdigest()
        assert (
            s.checksum == "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_provenance_fields(self):
        s = snap()
        assert (s.url, s.retrieved_at, s.content_type) == (
            "https://example.test/x",
            NOW,
            "text/csv",
        )

    def test_empty_body_rejected(self):
        with pytest.raises(SnapshotValidationError, match="empty"):
            snap(b"")

    @pytest.mark.parametrize(
        "ctype", ["text/html", "text/html; charset=utf-8", "application/json", "application/xml"]
    )
    def test_error_page_content_types_rejected(self, ctype):
        with pytest.raises(SnapshotValidationError, match="content type"):
            snap(**{"content-type": ctype})

    @pytest.mark.parametrize(
        "ctype", ["text/csv", "text/csv; charset=utf-8", "text/plain", "application/octet-stream"]
    )
    def test_data_content_types_accepted(self, ctype):
        assert snap(**{"content-type": ctype}).content_type == ctype.split(";")[0]

    def test_missing_content_type_is_tolerated(self):
        result = HttpResult(200, {}, b"a,b\n", "u")
        assert build_snapshot(result, url="u", now=NOW).content_type is None

    def test_truncated_download_detected_via_content_length(self):
        with pytest.raises(SnapshotValidationError, match="Truncated"):
            snap(b"a,b\n", **{"content-length": "9999"})

    def test_content_length_ignored_when_content_encoded(self):
        # With gzip the declared length is the compressed size and cannot be compared.
        assert snap(b"a,b\n", **{"content-length": "9999", "content-encoding": "gzip"})

    def test_last_modified_becomes_source_published_at(self):
        s = snap(**{"last-modified": "Wed, 28 Oct 2026 08:00:00 GMT"})
        assert s.source_published_at == datetime(2026, 10, 28, 8, 0, tzinfo=UTC)

    def test_invalid_last_modified_is_ignored(self):
        assert snap(**{"last-modified": "yesterday-ish"}).source_published_at is None

    def test_no_last_modified_means_unknown_not_now(self):
        assert snap().source_published_at is None
        assert NOW - timedelta(days=1) < snap().retrieved_at  # retrieval time is separate
