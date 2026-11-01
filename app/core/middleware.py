"""Pure-ASGI middleware: request context/logging and request-size limiting."""

from __future__ import annotations

import json
import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging_config import get_logger

log = get_logger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_QUIET_PATHS = {"/health", "/ready"}


class RequestContextMiddleware:
    """Assigns a request id, binds it to log context, and logs one line per request.

    Query strings are deliberately not logged (they may carry personal data or tokens).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id, method=scope["method"], path=scope["path"]
        )
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [h for h in message.get("headers", []) if h[0].lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            emit = log.debug if scope["path"] in _QUIET_PATHS else log.info
            emit("http_request", status=status, duration_ms=duration_ms)
            structlog.contextvars.clear_contextvars()


class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Rejects bodies over max_bytes, both via Content-Length and while streaming."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > self.max_bytes
            except ValueError:
                await self._reject(scope, send, 400, "bad_request", "Invalid Content-Length.")
                return
            if too_big:
                await self._reject(scope, send, 413, "payload_too_large", "Request body too large.")
                return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await self._reject(scope, send, 413, "payload_too_large", "Request body too large.")

    @staticmethod
    async def _reject(scope: Scope, send: Send, status: int, code: str, message: str) -> None:
        request_id = scope.get("state", {}).get("request_id")
        body = json.dumps(
            {"error": {"code": code, "message": message, "request_id": request_id}}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
