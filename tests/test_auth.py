from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.core.auth import TokenVerifier
from app.core.errors import AppError
from app.main import create_app
from tests.conftest import JWT_SECRET, SUPABASE_URL, make_settings

USER_ID = str(uuid.uuid4())


def hs_token(**overrides) -> str:
    claims = {
        "sub": USER_ID,
        "aud": "authenticated",
        "role": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "email": "reader@example.org",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    secret = claims.pop("_secret", JWT_SECRET)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, secret, algorithm="HS256")


@pytest.fixture
def verifier(settings):
    return TokenVerifier(settings)


def code_of(exc: pytest.ExceptionInfo[AppError]) -> str:
    return exc.value.code


def test_valid_hs256_token(verifier):
    user = verifier.verify(hs_token())
    assert str(user.id) == USER_ID
    assert user.email == "reader@example.org"
    assert user.db_claims["role"] == "authenticated"


def test_expired_token(verifier):
    with pytest.raises(AppError) as exc:
        verifier.verify(hs_token(exp=int(time.time()) - 3600))
    assert code_of(exc) == "token_expired"
    assert exc.value.status_code == 401


def test_wrong_secret(verifier):
    with pytest.raises(AppError) as exc:
        verifier.verify(hs_token(_secret="x" * 48))
    assert code_of(exc) == "invalid_token"


def test_wrong_audience(verifier):
    with pytest.raises(AppError):
        verifier.verify(hs_token(aud="something-else"))


def test_wrong_issuer(verifier):
    with pytest.raises(AppError):
        verifier.verify(hs_token(iss="https://evil.example/auth/v1"))


def test_anon_key_token_rejected(verifier):
    # The public anon key is itself a JWT with role=anon; it must never authenticate a user.
    with pytest.raises(AppError):
        verifier.verify(hs_token(role="anon"))


def test_service_role_token_rejected(verifier):
    with pytest.raises(AppError):
        verifier.verify(hs_token(role="service_role"))


def test_missing_sub_rejected(verifier):
    with pytest.raises(AppError):
        verifier.verify(hs_token(sub=None))


def test_non_uuid_sub_rejected(verifier):
    with pytest.raises(AppError):
        verifier.verify(hs_token(sub="not-a-uuid"))


def test_alg_none_rejected(verifier):
    token = jwt.encode(
        {"sub": USER_ID, "aud": "authenticated", "role": "authenticated"}, None, algorithm="none"
    )
    with pytest.raises(AppError) as exc:
        verifier.verify(token)
    assert code_of(exc) == "invalid_token"


def test_garbage_token(verifier):
    with pytest.raises(AppError):
        verifier.verify("not.a.jwt")


def test_hs256_rejected_when_no_secret_configured():
    v = TokenVerifier(make_settings(supabase_jwt_secret=""))
    with pytest.raises(AppError):
        v.verify(hs_token())


def test_es256_token_verified_via_jwks():
    private = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        {
            "sub": USER_ID,
            "aud": "authenticated",
            "role": "authenticated",
            "iss": f"{SUPABASE_URL}/auth/v1",
            "exp": int(time.time()) + 600,
        },
        private,
        algorithm="ES256",
        headers={"kid": "k1"},
    )
    v = TokenVerifier(make_settings(supabase_jwt_secret=""))
    v._jwks_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=private.public_key())
    )
    assert str(v.verify(token).id) == USER_ID


def test_es256_token_signed_by_other_key_rejected():
    signer = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        {
            "sub": USER_ID,
            "aud": "authenticated",
            "role": "authenticated",
            "iss": f"{SUPABASE_URL}/auth/v1",
            "exp": int(time.time()) + 600,
        },
        signer,
        algorithm="ES256",
        headers={"kid": "k1"},
    )
    v = TokenVerifier(make_settings(supabase_jwt_secret=""))
    v._jwks_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=other.public_key())
    )
    with pytest.raises(AppError):
        v.verify(token)


# ---------------------------------------------------------------- HTTP-level behaviour
def test_me_requires_authentication(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.json()["error"]["code"] == "unauthorized"


def test_me_rejects_bad_token(client):
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_me_without_database_returns_503_not_500(client):
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {hs_token()}"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "database_not_configured"


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeConn:
    def __init__(self, role, executed):
        self.role = role
        self.executed = executed

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((self.role, sql, params))
        if "from public.user_roles" in sql:
            return _FakeResult((1,) if self.role == "service" and self.admin else None)
        if "from public.profiles" in sql:
            return _FakeResult(
                {
                    "preferred_name": "Sam",
                    "preferred_country": "PK",
                    "preferred_region": None,
                    "preferred_language": "en",
                    "timezone": "Asia/Karachi",
                    "updated_at": "2026-10-29T00:00:00Z",
                }
            )
        return _FakeResult(None)

    admin = False


class _FakeDb:
    def __init__(self, admin=False):
        self.executed = []
        self.admin = admin
        self.sessions = []

    @asynccontextmanager
    async def session(self, role="service", *, claims=None, read_only=False):
        self.sessions.append((role, claims))
        conn = _FakeConn(role, self.executed)
        conn.admin = self.admin
        yield conn


def _client_with_fake_db(settings, fake):
    app = create_app(settings)
    app.dependency_overrides[get_db] = lambda: fake
    return TestClient(app, raise_server_exceptions=False)


def test_me_runs_profile_query_as_authenticated_role_with_verified_claims(settings):
    fake = _FakeDb(admin=False)
    c = _client_with_fake_db(settings, fake)
    r = c.get("/api/v1/me", headers={"Authorization": f"Bearer {hs_token()}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == USER_ID
    assert body["is_admin"] is False
    assert body["profile"]["timezone"] == "Asia/Karachi"
    role, claims = fake.sessions[0]
    assert role == "authenticated"
    assert claims["sub"] == USER_ID


def test_admin_flag_comes_from_database_not_token(settings):
    fake = _FakeDb(admin=True)
    c = _client_with_fake_db(settings, fake)
    # A token that merely *claims* admin must not matter, and one without claims must still work.
    r = c.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {hs_token(app_metadata={'role': 'admin'})}"},
    )
    assert r.json()["is_admin"] is True
    fake2 = _FakeDb(admin=False)
    c2 = _client_with_fake_db(settings, fake2)
    r2 = c2.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {hs_token(app_metadata={'role': 'admin'})}"},
    )
    assert r2.json()["is_admin"] is False


def test_anonymous_supabase_users_are_rejected(settings):
    c = _client_with_fake_db(settings, _FakeDb())
    r = c.get("/api/v1/me", headers={"Authorization": f"Bearer {hs_token(is_anonymous=True)}"})
    assert r.status_code == 403
