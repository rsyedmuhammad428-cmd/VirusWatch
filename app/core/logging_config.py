"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_SENSITIVE_FRAGMENTS = ("key", "token", "secret", "password", "authorization", "cookie", "jwt")
_REDACTED = "***"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (_REDACTED if _is_sensitive(k) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def _is_sensitive(key: object) -> bool:
    return isinstance(key, str) and any(f in key.lower() for f in _SENSITIVE_FRAGMENTS)


def redact_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key == "event":
            continue
        event_dict[key] = _REDACTED if _is_sensitive(key) else _redact(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    head: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[
            *head,
            redact_processor,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        # Records from stdlib loggers (e.g. ingestion): include `extra=` fields, then redact.
        foreign_pre_chain=[*head, structlog.stdlib.ExtraAdder(), redact_processor],
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # We log requests ourselves (without query strings). HTTP client loggers print full URLs,
    # which can contain API keys for some providers, so keep them quiet.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
