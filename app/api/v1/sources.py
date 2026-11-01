"""Data-source metadata and ingestion status."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import public_conn, require_admin, service_conn
from app.api.v1.common import fetch
from app.core.auth import AuthenticatedUser
from app.surveillance import queries, service
from app.surveillance import schemas as s

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=s.SourcesResponse, summary="Registered data sources")
async def list_sources(conn: Annotated[AsyncConnection, Depends(public_conn)]) -> s.SourcesResponse:
    """Publisher, URL, update cadence and whether the source's terms have been reviewed."""
    return service.build_sources(await fetch(conn, queries.SOURCES, {}), datetime.now(UTC))


@router.get("/status", response_model=s.SourceStatusResponse, summary="Data freshness per source")
async def source_status(
    conn: Annotated[AsyncConnection, Depends(service_conn)],
) -> s.SourceStatusResponse:
    """Last successful run and newest reporting period per source. Error text is not exposed."""
    return service.build_source_status(
        await fetch(conn, queries.SOURCE_STATUS, {}), datetime.now(UTC)
    )


@router.get("/{source_id}/runs", response_model=s.RunsResponse, summary="Ingestion runs (admin)")
async def source_runs(
    source_id: UUID,
    _: Annotated[AuthenticatedUser, Depends(require_admin)],
    conn: Annotated[AsyncConnection, Depends(service_conn)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> s.RunsResponse:
    rows = await fetch(conn, queries.SOURCE_RUNS, {"source_id": str(source_id), "limit": limit})
    return service.build_runs(rows, datetime.now(UTC))
