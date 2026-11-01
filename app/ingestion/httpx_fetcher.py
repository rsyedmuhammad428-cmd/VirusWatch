"""Production HttpFetcher backed by httpx. Thin on purpose: policy lives in app.ingestion.http."""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType

import httpx

from app.ingestion.errors import FetchError, TransientFetchError
from app.ingestion.http import HttpResult


class HttpxFetcher:
    def __init__(
        self, *, user_agent: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept": "text/csv, */*;q=0.5"},
            follow_redirects=True,
            max_redirects=3,
            transport=transport,
        )

    async def __aenter__(self) -> HttpxFetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None,
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResult:
        try:
            async with self._client.stream(
                "GET", url, params=params, timeout=timeout_seconds
            ) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                if response.status_code >= 400:
                    # Do not read error bodies: they are not data and may be large or hostile.
                    return HttpResult(response.status_code, headers, b"", str(response.url))

                declared = headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError(f"Download larger than the {max_bytes}-byte limit.")
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise FetchError(f"Download exceeded the {max_bytes}-byte limit.")
                    chunks.append(chunk)
                return HttpResult(
                    response.status_code, headers, b"".join(chunks), str(response.url)
                )
        except httpx.TimeoutException as exc:
            raise TransientFetchError(f"Timed out after {timeout_seconds:g}s") from exc
        except httpx.TransportError as exc:
            # str(exc) can contain the full URL; the class name is enough for diagnosis.
            raise TransientFetchError(f"Network error ({type(exc).__name__})") from exc
