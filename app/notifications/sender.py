"""Outbound email through Resend (https://api.resend.com/emails).

The HTTP call goes through a small injected `post` callable so the request shape and error
mapping are unit-tested without a network. The real adapter (HttpxPoster) has not been run
against Resend: no API key or verified sending domain was available when this was written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

RESEND_URL = "https://api.resend.com/emails"


class EmailSendError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class OutboundEmail:
    to: str
    subject: str
    html: str
    text: str
    unsubscribe_url: str | None = None


class EmailSender(Protocol):
    async def send(self, message: OutboundEmail) -> str:
        """Send and return the provider's message id."""
        ...


class Poster(Protocol):
    async def __call__(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, object],
        timeout: float,
    ) -> tuple[int, Mapping[str, object]]: ...


class ResendSender:
    def __init__(
        self, api_key: str, from_address: str, post: Poster, timeout: float = 15.0
    ) -> None:
        if not api_key or not from_address:
            raise ValueError("api_key and from_address are required")
        self._key, self._from, self._post, self._timeout = api_key, from_address, post, timeout

    async def send(self, message: OutboundEmail) -> str:
        body: dict[str, object] = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if message.unsubscribe_url:
            body["headers"] = {"List-Unsubscribe": f"<{message.unsubscribe_url}>"}
        try:
            status, payload = await self._post(
                RESEND_URL,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise EmailSendError("timeout", retryable=True) from exc
        except OSError as exc:
            raise EmailSendError("network_error", retryable=True) from exc
        if status in (429,) or status >= 500:
            raise EmailSendError(f"provider_http_{status}", retryable=True)
        if status >= 400:
            raise EmailSendError(f"provider_http_{status}", retryable=False)
        message_id = payload.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise EmailSendError("provider_response_without_id", retryable=False)
        return message_id


class HttpxPoster:
    """Production Poster (needs httpx; not exercised in the build sandbox)."""

    async def __call__(
        self, url: str, *, headers: Mapping[str, str], json: Mapping[str, object], timeout: float
    ) -> tuple[int, Mapping[str, object]]:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=dict(headers), json=json)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response.status_code, payload if isinstance(payload, dict) else {}
