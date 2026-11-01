"""Shared fixtures.

Web-stack imports are deferred into the fixtures so that tests which only exercise pure logic
(e.g. tests/ingestion) can be collected and run without FastAPI/pydantic-settings installed.
"""

from __future__ import annotations

import pytest

JWT_SECRET = "unit-test-hs256-secret-0123456789-abcdefghij"  # 43 chars; test-only
SUPABASE_URL = "https://testproject.supabase.co"


def make_settings(**overrides):
    from app.config import Settings

    base = {
        "environment": "test",
        "supabase_url": SUPABASE_URL,
        "supabase_jwt_secret": JWT_SECRET,
        "allowed_origins": "http://localhost:5173",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def app(settings):
    from app.main import create_app

    return create_app(settings)


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    # raise_server_exceptions=False so unhandled errors are returned as the 500 envelope.
    return TestClient(app, raise_server_exceptions=False)
