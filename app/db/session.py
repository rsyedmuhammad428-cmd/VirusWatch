"""Async database access (SQLAlchemy 2.x Core + asyncpg) with Postgres-enforced RLS.

Three access modes, all inside a single transaction:

  service        privileged backend connection (ingestion, analysis, admin). Bypasses RLS.
  anon           SET LOCAL ROLE anon          -> public endpoints; only RLS-approved rows.
  authenticated  SET LOCAL ROLE authenticated -> user endpoints; auth.uid() = verified JWT sub,
                 so RLS (not just application code) isolates one user's rows from another's.

All SQL is parameterised. Role names come from a fixed allow-list, never from input.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

DbRole = Literal["service", "anon", "authenticated"]
_SET_ROLE_SQL = {
    "anon": "set local role anon",
    "authenticated": "set local role authenticated",
}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def build_engine(database_url: str, *, pool_size: int = 5) -> AsyncEngine:
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")

    connect_args: dict[str, Any] = {
        # Supabase's pooler runs pgbouncer in transaction mode: no server-side prepared statements.
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        "command_timeout": 30,
        # No startup parameters (statement_timeout etc.): some poolers reject them. The timeout
        # is applied per transaction with SET LOCAL in Database.session().
    }
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    if sslmode and sslmode != "disable":
        connect_args["ssl"] = sslmode
    elif (
        sslmode is None
        and (url.host or "") not in _LOCAL_HOSTS
        and not str(url.host).startswith("/")
    ):
        connect_args["ssl"] = "require"
    url = url.set(query=query)

    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
    )


class Database:
    def __init__(self, database_url: str, *, pool_size: int = 5) -> None:
        self.engine = build_engine(database_url, pool_size=pool_size)

    @asynccontextmanager
    async def session(
        self,
        role: DbRole = "service",
        *,
        claims: dict[str, Any] | None = None,
        read_only: bool = False,
        rollback: bool = False,
    ) -> AsyncIterator[AsyncConnection]:
        """One transaction. Commits on success, rolls back on error.

        rollback=True discards all changes even on success (used for ingestion dry-runs, where
        the real statements must run but must not persist).
        """
        if role == "authenticated" and not claims:
            raise ValueError("authenticated sessions require verified JWT claims")
        if rollback and read_only:
            raise ValueError("rollback=True is meaningless for a read-only session")
        async with self.engine.connect() as conn:
            transaction = await conn.begin()
            try:
                if read_only or role == "anon":
                    # Must be the first statement of the transaction.
                    await conn.execute(text("set transaction read only"))
                await conn.execute(text("set local statement_timeout = '30s'"))
                if role != "service":
                    await conn.execute(
                        text("select set_config('request.jwt.claims', :claims, true)"),
                        {"claims": json.dumps(claims or {"role": role})},
                    )
                    await conn.execute(text(_SET_ROLE_SQL[role]))
                yield conn
            except BaseException:
                await transaction.rollback()
                raise
            if rollback:
                await transaction.rollback()
            else:
                await transaction.commit()

    async def check_ready(self) -> dict[str, str]:
        """Return per-check status strings; never raises and never leaks driver errors."""
        checks = {"database": "error", "schema": "unknown"}
        try:
            async with self.session(read_only=True) as conn:
                await conn.execute(text("select 1"))
                checks["database"] = "ok"
                applied = (
                    await conn.execute(
                        text("select to_regclass('public.observations') is not null")
                    )
                ).scalar_one()
                checks["schema"] = "ok" if applied else "migrations_not_applied"
        except Exception:
            from app.core.logging_config import get_logger

            get_logger(__name__).warning("readiness_check_failed", exc_info=True)
        return checks

    async def dispose(self) -> None:
        await self.engine.dispose()
