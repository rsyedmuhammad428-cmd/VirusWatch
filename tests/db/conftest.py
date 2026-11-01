from __future__ import annotations

import os
import shutil
import uuid

import pytest

from tests.db.psql_harness import create_migrated_database, drop_database


@pytest.fixture(scope="session")
def pg():
    """A fresh, fully migrated, throw-away database (skipped when no test server is configured)."""
    admin = os.environ.get("TEST_PG_ADMIN_CONN")
    if not admin:
        pytest.skip(
            "TEST_PG_ADMIN_CONN not set (a superuser connection to a disposable PostgreSQL)"
        )
    if shutil.which("psql") is None:
        pytest.skip("psql client not installed")
    db = create_migrated_database(
        admin,
        f"vw_pytest_{uuid.uuid4().hex[:10]}",
        no_pgvector=os.environ.get("NO_PGVECTOR") == "1",
    )
    yield db
    drop_database(db)


@pytest.fixture(autouse=True)
def clean(request):
    """Reset mutable ingestion and analysis tables before each DB test (seed data is left alone)."""
    if "pg" in request.fixturenames:
        pg = request.getfixturevalue("pg")
        pg.script(
            "truncate public.analysis_results, public.observation_revisions, "
            "public.observations, public.ingestion_runs "
            "restart identity cascade;"
        )
