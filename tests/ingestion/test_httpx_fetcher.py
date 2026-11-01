"""The production HTTP adapter, exercised against httpx's in-process MockTransport (no network)."""

from __future__ import annotations

import asyncio

import pytest

httpx = pytest.importorskip("httpx")

from app.ingestion.errors import FetchError, TransientFetchError  # noqa: E402
from app.ingestion.http import RetryPolicy, fetch_with_retries  # noqa: E402
from app.ingestion.httpx_fetcher import HttpxFetcher  # noqa: E402


def get(handler, *, max_bytes=1000, timeout=5, url="https://example.test/data", params=None):
    async def go():
        async with HttpxFetcher(
            user_agent="VirusWatch-Test/1.0", transport=httpx.MockTransport(handler)
        ) as f:
            return await f.get(url, params=params, max_bytes=max_bytes, timeout_seconds=timeout)

    return asyncio.run(go())


def test_success_returns_body_status_and_lowercase_headers():
    result = get(
        lambda r: httpx.Response(200, content=b"a,b\n1,2\n", headers={"Content-Type": "text/csv"})
    )
    assert result.status == 200 and result.body == b"a,b\n1,2\n"
    assert result.headers["content-type"] == "text/csv"


def test_user_agent_and_query_params_are_sent():
    seen = {}

    def handler(request):
        seen["ua"], seen["url"] = request.headers["user-agent"], str(request.url)
        return httpx.Response(200, content=b"x")

    get(handler, params={"$format": "csv", "$filter": "A eq 'B'"})
    assert seen["ua"] == "VirusWatch-Test/1.0"
    assert "%24format=csv" in seen["url"] and "%24filter=" in seen["url"]


def test_error_status_is_returned_not_raised_and_body_is_not_read():
    result = get(
        lambda r: httpx.Response(503, content=b"<html>" + b"x" * 5000, headers={"Retry-After": "3"})
    )
    assert result.status == 503 and result.body == b"" and result.headers["retry-after"] == "3"


def test_timeout_becomes_a_transient_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TransientFetchError, match="Timed out"):
        get(handler)


def test_connection_error_is_transient_and_does_not_leak_the_url():
    def handler(request):
        raise httpx.ConnectError(
            "could not connect to https://secret.internal/path?key=abc", request=request
        )

    with pytest.raises(TransientFetchError) as exc:
        get(handler)
    assert "secret.internal" not in str(exc.value) and "key=abc" not in str(exc.value)
    assert "ConnectError" in str(exc.value)


def test_declared_oversize_is_rejected_before_reading():
    with pytest.raises(FetchError, match="larger than") as exc:
        get(
            lambda r: httpx.Response(200, content=b"x" * 50, headers={"Content-Length": "5000"}),
            max_bytes=100,
        )
    assert not isinstance(exc.value, TransientFetchError)


def test_streamed_oversize_without_content_length_is_rejected():
    def handler(request):
        async def body():
            for _ in range(10):
                yield b"x" * 100

        return httpx.Response(200, content=body())

    with pytest.raises(FetchError, match="exceeded"):
        get(handler, max_bytes=250)


def test_body_exactly_at_the_limit_is_accepted():
    assert len(get(lambda r: httpx.Response(200, content=b"x" * 100), max_bytes=100).body) == 100


def test_redirects_are_followed_to_the_final_url():
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(302, headers={"Location": "https://example.test/new"})
        return httpx.Response(200, content=b"moved")

    result = get(handler, url="https://example.test/old")
    assert result.body == b"moved" and result.url.endswith("/new")


def test_retry_layer_recovers_through_the_real_adapter():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, content=b"ok")

    async def go():
        async with HttpxFetcher(user_agent="t", transport=httpx.MockTransport(handler)) as fetcher:
            return await fetch_with_retries(
                fetcher,
                "https://example.test/x",
                max_bytes=100,
                timeout_seconds=5,
                policy=RetryPolicy(max_attempts=4, base_delay=0, max_delay=0),
            )

    assert asyncio.run(go()).body == b"ok" and calls["n"] == 3
