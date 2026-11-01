"""FastAPI dependencies: settings, database, authentication, authorization."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.core.auth import AuthenticatedUser, TokenVerifier
from app.core.errors import AppError
from app.db import Database

_bearer = HTTPBearer(auto_error=False, description="Supabase Auth access token (JWT)")


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    db: Database | None = getattr(request.app.state, "db", None)
    if db is None:
        raise AppError(503, "database_not_configured", "The database is not configured.")
    return db


def get_verifier(request: Request) -> TokenVerifier:
    return request.app.state.token_verifier


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[TokenVerifier, Depends(get_verifier)],
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            401, "unauthorized", "Authentication required.", headers={"WWW-Authenticate": "Bearer"}
        )
    return await verifier.averify(credentials.credentials)


async def require_user(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    if user.is_anonymous:
        raise AppError(403, "forbidden", "A registered account is required.")
    return user


async def is_admin(db: Database, user: AuthenticatedUser) -> bool:
    async with db.session(read_only=True) as conn:
        row = (
            await conn.execute(
                text("select 1 from public.user_roles where user_id = :uid and role = 'admin'"),
                {"uid": user.id},
            )
        ).first()
    return row is not None


async def require_admin(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
    db: Annotated[Database, Depends(get_db)],
) -> AuthenticatedUser:
    if not await is_admin(db, user):
        raise AppError(403, "forbidden", "Administrator access required.")
    return user


async def user_conn(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
    db: Annotated[Database, Depends(get_db)],
) -> AsyncIterator[AsyncConnection]:
    """Connection running as role `authenticated` with the caller's claims: RLS applies."""
    async with db.session("authenticated", claims=user.db_claims) as conn:
        yield conn


async def public_conn(db: Annotated[Database, Depends(get_db)]) -> AsyncIterator[AsyncConnection]:
    """Read-only connection running as role `anon`: only RLS-approved public rows are visible."""
    async with db.session("anon", read_only=True) as conn:
        yield conn


async def service_conn(db: Annotated[Database, Depends(get_db)]) -> AsyncIterator[AsyncConnection]:
    """Read-only backend connection. Only for endpoints that expose a deliberately chosen subset
    of operational data (never pass its rows to a client unfiltered)."""
    async with db.session("service", read_only=True) as conn:
        yield conn
