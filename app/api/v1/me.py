from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import get_db, is_admin, require_user, user_conn
from app.core.auth import AuthenticatedUser
from app.db import Database

router = APIRouter(prefix="/me", tags=["user"])


class ProfileOut(BaseModel):
    preferred_name: str | None
    preferred_country: str | None
    preferred_region: str | None
    preferred_language: str
    timezone: str
    updated_at: datetime


class MeOut(BaseModel):
    id: UUID
    email: str | None
    is_admin: bool
    profile: ProfileOut | None


@router.get("", response_model=MeOut, summary="Current user and profile")
async def get_me(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
    conn: Annotated[AsyncConnection, Depends(user_conn)],
    db: Annotated[Database, Depends(get_db)],
) -> MeOut:
    # Runs as Postgres role `authenticated`: RLS guarantees only this user's row can match,
    # independent of the WHERE clause.
    row = (
        (
            await conn.execute(
                text(
                    "select preferred_name, preferred_country, preferred_region, "
                    "preferred_language, timezone, updated_at "
                    "from public.profiles where id = :uid"
                ),
                {"uid": user.id},
            )
        )
        .mappings()
        .first()
    )
    return MeOut(
        id=user.id,
        email=user.email,
        is_admin=await is_admin(db, user),
        profile=ProfileOut(**row) if row else None,
    )
