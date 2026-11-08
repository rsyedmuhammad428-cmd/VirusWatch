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


class LoggingSender:
    """The safe development-mode sender.

    With no `redirect_to`, nothing is sent anywhere: the call is logged (recipient and subject
    only -- never the HTML/text body, which could carry a real surveillance claim) and a
    deterministic fake message id is returned, so callers that record `provider_message_id`
    still have something stable to store. With `redirect_to`, the same message is actually sent
    through `inner` (a real `ResendSender`) but to that one fixed address instead of the real
    recipient, so a developer can see a rendered email without ever reaching a real user.
    """

    def __init__(self, *, inner: EmailSender | None = None, redirect_to: str | None = None) -> None:
        self._inner = inner
        self._redirect_to = redirect_to

    async def send(self, message: OutboundEmail) -> str:
        import logging

        logger = logging.getLogger("app.notifications.dev_sender")
        if self._redirect_to and self._inner is not None:
            logger.info(
                "dev email redirected", extra={"to": self._redirect_to, "subject": message.subject}
            )
            redirected = OutboundEmail(
                to=self._redirect_to,
                subject=f"[dev:{message.to}] {message.subject}",
                html=message.html,
                text=message.text,
                unsubscribe_url=message.unsubscribe_url,
            )
            return await self._inner.send(redirected)
        logger.info("dev email suppressed", extra={"to": message.to, "subject": message.subject})
        return f"dev-{abs(hash((message.to, message.subject)))}"


def choose_sender(
    *,
    api_key: str,
    from_address: str,
    environment: str,
    domain_verified: bool,
    mode: str = "auto",
    redirect_to: str | None = None,
) -> EmailSender:
    """The single, fail-closed policy for picking a sender, used by both the CLI's `notify`
    command and the `POST /api/v1/me/notifications/test` endpoint.

    mode="auto" (the default): live delivery only when environment == "production" AND the
    sending domain is confirmed verified; dev mode (suppressed, or redirected if `redirect_to`
    is set) in every other case. mode="live" and mode="dev" are explicit operator overrides.
    Raises ValueError if the chosen mode needs credentials that are not configured, so a
    misconfigured deployment fails loudly instead of silently falling back to dev mode.
    """
    use_live = mode == "live" or (
        mode == "auto" and environment == "production" and domain_verified
    )
    if use_live:
        if not api_key or not from_address:
            raise ValueError("RESEND_API_KEY and EMAIL_FROM must be set to send live email.")
        return ResendSender(api_key, from_address, HttpxPoster())
    if redirect_to:
        if not api_key or not from_address:
            raise ValueError("RESEND_API_KEY and EMAIL_FROM must be set to redirect dev email.")
        return LoggingSender(
            inner=ResendSender(api_key, from_address, HttpxPoster()), redirect_to=redirect_to
        )
    return LoggingSender()
