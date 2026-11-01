"""Resend webhook verification (Svix signature scheme) and event parsing.

Verified against Resend's documented scheme: headers svix-id / svix-timestamp / svix-signature,
HMAC-SHA256 over "<id>.<timestamp>.<raw body>" keyed with the base64-decoded part of the
"whsec_..." secret, signatures as space-separated "v1,<base64>" entries, 5-minute tolerance.
This has been unit-tested with locally generated signatures only; it has not been exercised
against a real Resend account.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

TOLERANCE = timedelta(minutes=5)
_PAYLOAD_DATA_ALLOWLIST = ("email_id", "created_at", "bounce", "failed", "subject")


class WebhookVerificationError(Exception):
    """The request is not an authentic, fresh Resend webhook."""


class WebhookPayloadError(ValueError):
    """The body is authentic but not a usable event."""


def _key(secret: str) -> bytes:
    raw = secret.removeprefix("whsec_")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WebhookVerificationError("Webhook secret is not valid base64.") from exc


def sign(secret: str, svix_id: str, svix_timestamp: str, body: bytes) -> str:
    """Compute a signature header value (used by tests and local replay tooling)."""
    signed = f"{svix_id}.{svix_timestamp}.".encode() + body
    digest = hmac.new(_key(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def verify(
    secret: str,
    *,
    svix_id: str | None,
    svix_timestamp: str | None,
    svix_signature: str | None,
    body: bytes,
    now: datetime,
) -> None:
    if not secret:
        raise WebhookVerificationError("Webhook secret is not configured.")
    if not (svix_id and svix_timestamp and svix_signature):
        raise WebhookVerificationError("Missing signature headers.")
    try:
        sent_at = datetime.fromtimestamp(int(svix_timestamp), tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise WebhookVerificationError("Invalid timestamp.") from exc
    if abs(now.astimezone(UTC) - sent_at) > TOLERANCE:
        raise WebhookVerificationError("Timestamp outside tolerance.")
    expected = sign(secret, svix_id, svix_timestamp, body)
    for candidate in svix_signature.split():
        if hmac.compare_digest(candidate, expected):
            return
    raise WebhookVerificationError("Signature mismatch.")


@dataclass(frozen=True)
class ResendEvent:
    event_type: str
    email_id: str | None
    payload: dict[str, object]  # trimmed: recipient addresses are never stored


def parse_event(body: bytes) -> ResendEvent:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise WebhookPayloadError("Body is not JSON.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise WebhookPayloadError("Missing event type.")
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    email_id = inner.get("email_id")
    kept = {k: inner[k] for k in _PAYLOAD_DATA_ALLOWLIST if k in inner}
    payload: dict[str, object] = {"type": data["type"], "created_at": data.get("created_at")}
    payload["data"] = kept
    return ResendEvent(data["type"], email_id if isinstance(email_id, str) else None, payload)
