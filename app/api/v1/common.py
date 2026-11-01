"""Helpers shared by the v1 routers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def fetch(conn: AsyncConnection, statement: str, params: dict[str, Any]) -> list[Any]:
    """Run one read-only statement with bound parameters; rows behave like mappings."""
    return list((await conn.execute(text(statement), params)).mappings().all())
