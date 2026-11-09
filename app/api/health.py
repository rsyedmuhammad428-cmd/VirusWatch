from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.config import Settings
from app.db import Database

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe (no dependencies checked)")
async def health() -> dict[str, str]:
    return {"status": "healthy", "version": __version__}


@router.get("/ready", summary="Readiness probe (database + schema)")
async def ready(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        checks = {"database": "not_configured", "schema": "unknown"}
    else:
        checks = await db.check_ready()
    ok = checks["database"] == "ok" and checks["schema"] == "ok"
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ready" if ok else "not_ready",
            "environment": settings.environment,
            "checks": checks,
        },
    )
