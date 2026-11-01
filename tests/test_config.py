import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_origins_are_parsed_and_trailing_slash_removed():
    s = make_settings(allowed_origins="http://a.example/, https://b.example")
    assert s.cors_origins == ["http://a.example", "https://b.example"]


def test_wildcard_origin_rejected():
    with pytest.raises(ValidationError):
        make_settings(allowed_origins="*")


def test_malformed_origin_rejected():
    with pytest.raises(ValidationError):
        make_settings(allowed_origins="localhost:5173")


def test_production_requires_core_settings():
    with pytest.raises(ValidationError):
        make_settings(environment="production", supabase_url="", allowed_origins="")


def test_production_rejects_http_origin():
    with pytest.raises(ValidationError):
        make_settings(
            environment="production",
            supabase_db_url="postgresql://u:p@db.example:5432/postgres",
            allowed_origins="http://insecure.example",
        )


def test_production_valid():
    s = make_settings(
        environment="production",
        supabase_db_url="postgresql://u:p@db.example:5432/postgres",
        allowed_origins="https://app.example",
    )
    assert s.is_production


def test_secrets_not_in_repr():
    s = make_settings(supabase_service_role_key="super-secret-value")
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s.model_dump())


def test_issuer_and_jwks_urls():
    s = make_settings()
    assert s.jwt_issuer == "https://testproject.supabase.co/auth/v1"
    assert s.jwks_url.endswith("/auth/v1/.well-known/jwks.json")
