"""Runs the repository's real SQL statements against PostgreSQL using only the `psql` client.

Why: the SQL in app/ingestion/sql.py is the part of ingestion most likely to hide bugs (upsert,
revision trigger, uniqueness), and it must be tested against a real server. Using psql keeps these
tests free of any Python database driver. Bind parameters (`:name`) are substituted as properly
quoted SQL literals, which is faithful for the value types used here.

Connection: TEST_PG_ADMIN_CONN is a libpq keyword string for a *superuser* on a disposable server,
e.g.  "host=localhost port=5432 user=postgres password=postgres".
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
ROOT = Path(__file__).resolve().parents[2]


class PsqlError(RuntimeError):
    pass


def literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, datetime | date | UUID):
        value = value.isoformat() if not isinstance(value, UUID) else str(value)
    elif isinstance(value, dict | list):
        value = json.dumps(value)
    return "'" + str(value).replace("'", "''") + "'"


def render(statement: str, params: dict[str, Any] | None = None) -> str:
    params = params or {}
    needed = set(_PARAM.findall(statement))
    if needed != set(params):
        raise KeyError(
            f"parameters mismatch: statement needs {sorted(needed)}, got {sorted(params)}"
        )
    return _PARAM.sub(lambda m: literal(params[m.group(1)]), statement)


def _run(conn: str, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", conn, *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise PsqlError(done.stderr.strip())
    return done.stdout


class PsqlDatabase:
    def __init__(self, admin_conn: str, name: str) -> None:
        self.admin_conn, self.name = admin_conn, name
        self.conn = f"{admin_conn} dbname={name}"

    def execute(
        self, statement: str, params: dict[str, Any] | None = None, *, role: str | None = None
    ) -> list[dict[str, str]]:
        """One statement; returns its rows as dicts of strings (empty for non-SELECTs).

        `role` runs it as that database role (e.g. 'anon'), so row-level security applies.
        """
        text = render(statement, params)
        if role is not None:
            if not re.fullmatch(r"[a-z_]+", role):
                raise ValueError(f"unsafe role name: {role!r}")
            text = f"set role {role};\n{text}"
        out = _run(self.conn, "--csv", "-f", "-", stdin=text)
        return list(csv.DictReader(io.StringIO(out))) if out.strip() else []

    def script(self, text: str) -> str:
        """Several statements in one session (needed for transactions). Returns raw CSV output."""
        return _run(self.conn, "--csv", "-f", "-", stdin=text)

    def scalar(
        self, statement: str, params: dict[str, Any] | None = None, *, role: str | None = None
    ) -> str | None:
        rows = self.execute(statement, params, role=role)
        return next(iter(rows[0].values())) if rows else None


def create_migrated_database(admin_conn: str, name: str, *, no_pgvector: bool) -> PsqlDatabase:
    _run(f"{admin_conn} dbname=postgres", "-c", f'create database "{name}"')
    db = PsqlDatabase(admin_conn, name)
    _run(db.conn, "-f", str(ROOT / "tests/db/bootstrap.sql"))
    for migration in sorted((ROOT / "supabase/migrations").glob("*.sql")):
        sql = migration.read_text()
        if no_pgvector:  # see tests/db/no_pgvector.sed for exactly what changes
            sql = subprocess.run(
                ["sed", "-f", str(ROOT / "tests/db/no_pgvector.sed")],
                input=sql,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        _run(db.conn, "-f", "-", stdin=sql)
    return db


def drop_database(db: PsqlDatabase) -> None:
    _run(
        f"{db.admin_conn} dbname=postgres",
        "-c",
        f'drop database if exists "{db.name}" with (force)',
    )
