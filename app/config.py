"""Application settings, loaded from environment variables (and .env in development)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Stored as a plain string (comma separated) so env parsing never needs JSON.
    allowed_origins: str = ""
    frontend_base_url: str = ""
    max_request_bytes: int = Field(1_048_576, ge=1_024)

    supabase_url: str = ""
    supabase_anon_key: SecretStr = SecretStr("")
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_jwt_secret: SecretStr = SecretStr("")
    supabase_db_url: SecretStr = SecretStr("")

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = ""
    gemini_embedding_model: str = ""
    embedding_dimensions: int = 768

    # AI workflow (Phase 5). The provider is unconfigured (AskService falls back to an
    # insufficient-evidence answer, never a fabricated one) until gemini_api_key/gemini_model
    # are set to values confirmed against the current Gemini API documentation.
    ai_retrieval_match_count: int = Field(6, ge=1, le=50)
    ai_max_output_tokens: int = Field(1024, ge=1, le=8192)
    ai_request_timeout_seconds: float = Field(20.0, gt=0, le=120)
    rag_chunk_target_chars: int = Field(1200, ge=100, le=8000)
    rag_chunk_overlap_chars: int = Field(150, ge=0, le=2000)

    resend_api_key: SecretStr = SecretStr("")
    email_from: str = ""
    resend_webhook_secret: SecretStr = SecretStr("")
    # Manually set only after confirming domain verification in the Resend dashboard yourself;
    # nothing here calls Resend's API to check it. Gates live sending in "auto" mode together
    # with environment == "production" (see app.notifications.sender.choose_sender).
    resend_domain_verified: bool = False
    # Immediate alerts need a human-approved analysis result unless this is explicitly set.
    alert_accept_unreviewed: bool = False
    notification_batch_size: int = Field(100, ge=1, le=1000)

    # Ingestion. Identify yourself to data publishers: put a contact URL/email in the user agent.
    ingestion_user_agent: str = "VirusWatch-Ingestion/0.1"
    ingestion_default_countries: str = "PAK,USA"  # ISO3, comma separated
    ingestion_timeout_seconds: float = Field(120.0, gt=0, le=600)
    ingestion_max_attempts: int = Field(4, ge=1, le=8)
    ingestion_max_download_mb: int = Field(100, ge=1, le=1024)
    ingestion_max_reject_fraction: float = Field(0.2, ge=0, le=1)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator("supabase_url", "frontend_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @property
    def cors_origins(self) -> list[str]:
        origins = [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]
        for origin in origins:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                raise ValueError(
                    f"ALLOWED_ORIGINS entries must be explicit http(s) origins, got {origin!r}"
                )
        return origins

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def jwt_issuer(self) -> str | None:
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @property
    def jwks_url(self) -> str | None:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json" if self.supabase_url else None

    @model_validator(mode="after")
    def _validate_environment(self) -> Settings:
        origins = self.cors_origins  # raises on wildcard / malformed origins
        if self.environment in {"staging", "production"}:
            missing = [
                name
                for name, value in {
                    "SUPABASE_URL": self.supabase_url,
                    "SUPABASE_DB_URL": self.supabase_db_url.get_secret_value(),
                    "ALLOWED_ORIGINS": ",".join(origins),
                }.items()
                if not value
            ]
            if missing:
                raise ValueError(f"Missing required settings for {self.environment}: {missing}")
        if self.is_production and any(o.startswith("http://") for o in origins):
            raise ValueError("ALLOWED_ORIGINS must use https in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
