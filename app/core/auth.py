"""Server-side validation of Supabase Auth JWTs.

Supports both Supabase signing modes:
  * asymmetric signing keys (ES256 / RS256) verified against the project JWKS endpoint, and
  * the legacy shared secret (HS256) when SUPABASE_JWT_SECRET is configured.

The algorithm is chosen from an explicit allow-list; "none" and anything else is rejected.
Roles are never taken from request bodies. Admin status comes from public.user_roles.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    ExpiredSignatureError,
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)

from app.config import Settings
from app.core.errors import AppError
from app.core.logging_config import get_logger

log = get_logger(__name__)

ASYMMETRIC_ALGS = {"ES256", "RS256"}
SYMMETRIC_ALGS = {"HS256"}
_BEARER = {"WWW-Authenticate": "Bearer"}


def _unauthorized(code: str, message: str) -> AppError:
    return AppError(401, code, message, headers=_BEARER)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None
    role: str
    is_anonymous: bool = False
    claims: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def db_claims(self) -> dict[str, Any]:
        """Minimal claim set handed to Postgres so auth.uid() / RLS work for this user."""
        return {"sub": str(self.id), "role": "authenticated", "email": self.email}


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self._issuer = settings.jwt_issuer
        self._hs_secret = settings.supabase_jwt_secret.get_secret_value() or None
        self._jwks_client: PyJWKClient | None = (
            PyJWKClient(settings.jwks_url, cache_keys=True, lifespan=600, timeout=5)
            if settings.jwks_url
            else None
        )

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            alg = jwt.get_unverified_header(token).get("alg")
        except InvalidTokenError as exc:
            raise _unauthorized("invalid_token", "Invalid access token.") from exc

        if alg in SYMMETRIC_ALGS:
            if not self._hs_secret:
                raise _unauthorized("invalid_token", "Invalid access token.")
            key: Any = self._hs_secret
        elif alg in ASYMMETRIC_ALGS:
            if self._jwks_client is None:
                raise AppError(503, "auth_unavailable", "Authentication is not configured.")
            try:
                key = self._jwks_client.get_signing_key_from_jwt(token).key
            except PyJWKClientConnectionError as exc:
                log.warning("jwks_fetch_failed")
                raise AppError(
                    503, "auth_unavailable", "Authentication service unavailable."
                ) from exc
            except PyJWKClientError as exc:
                raise _unauthorized("invalid_token", "Invalid access token.") from exc
        else:
            raise _unauthorized("invalid_token", "Unsupported token algorithm.")

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience="authenticated",
                issuer=self._issuer,
                options={
                    "require": ["exp", "sub", "aud"],
                    "verify_iss": self._issuer is not None,
                },
                leeway=10,
            )
        except ExpiredSignatureError as exc:
            raise _unauthorized("token_expired", "Access token has expired.") from exc
        except InvalidTokenError as exc:
            raise _unauthorized("invalid_token", "Invalid access token.") from exc

        if claims.get("role") != "authenticated":
            raise _unauthorized("invalid_token", "Invalid access token.")
        try:
            user_id = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise _unauthorized("invalid_token", "Invalid access token.") from exc

        return AuthenticatedUser(
            id=user_id,
            email=claims.get("email"),
            role=claims["role"],
            is_anonymous=bool(claims.get("is_anonymous", False)),
            claims={k: claims[k] for k in ("sub", "email", "role", "exp", "iat") if k in claims},
        )

    async def averify(self, token: str) -> AuthenticatedUser:
        # PyJWKClient does blocking network IO on cache miss; keep it off the event loop.
        return await asyncio.to_thread(self.verify, token)
