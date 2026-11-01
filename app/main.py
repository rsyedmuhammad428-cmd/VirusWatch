"""VirusWatch API entrypoint.

Run with the application factory (no import-time side effects):
    uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.api.v1 import router as v1_router
from app.config import Settings, get_settings
from app.core.auth import TokenVerifier
from app.core.errors import register_exception_handlers
from app.core.logging_config import configure_logging, get_logger
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.db import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "development")
    log = get_logger("app")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("startup", environment=settings.environment, version=__version__)
        yield
        db: Database | None = getattr(app.state, "db", None)
        if db is not None:
            await db.dispose()
        log.info("shutdown")

    # Interactive docs are disabled in production; the OpenAPI schema stays available for Lovable.
    app = FastAPI(
        title="VirusWatch API",
        version=__version__,
        description=(
            "Infectious-disease surveillance information. Not a diagnostic tool: indicators are "
            "surveillance signals, not confirmed outbreaks."
        ),
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.token_verifier = TokenVerifier(settings)
    db_url = settings.supabase_db_url.get_secret_value()
    app.state.db = Database(db_url) if db_url else None

    # add_middleware: last added is outermost. Order: CORS > request context > size limit.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # Bearer tokens in headers, no cookies
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(v1_router)
    return app
