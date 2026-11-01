"""Notification preferences (schedule, alerts, consent), unsubscribe, and the Resend webhook."""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.api.deps import get_db, get_settings_dep, require_user
from app.config import Settings
from app.core.auth import AuthenticatedUser
from app.core.errors import AppError
from app.db import Database
from app.notifications.schedule import (
    DEFAULT_MONTHLY_DAY,
    DEFAULT_TIME,
    DEFAULT_TIMEZONE,
    DEFAULT_WEEKLY_DAY,
    DeliverySchedule,
    ScheduleError,
    next_occurrence,
)
from app.notifications.sql_repository import SqlNotificationRepository
from app.notifications.webhook import (
    WebhookPayloadError,
    WebhookVerificationError,
    parse_event,
    verify,
)

router = APIRouter(tags=["notifications"])
CONSENT_TEXT_VERSION = "2026-10-31"


class PreferencesIn(BaseModel):
    enabled: bool = False
    consent: bool = False  # the user ticked the consent box in this request
    digest_enabled: bool = True
    alerts_enabled: bool = False
    frequency: Literal["daily", "weekly", "monthly"] = "weekly"
    timezone: str = DEFAULT_TIMEZONE
    delivery_time: str = DEFAULT_TIME.strftime("%H:%M")  # local wall clock, HH:MM
    weekly_day: int = DEFAULT_WEEKLY_DAY  # 1 = Monday ... 7 = Sunday
    monthly_day: int = Field(DEFAULT_MONTHLY_DAY, ge=1, le=28)
    minimum_alert_severity: Literal["elevated", "possible_anomaly"] = "possible_anomaly"
    selected_diseases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("selected_diseases")
    @classmethod
    def _slugs(cls, v: list[str]) -> list[str]:
        import re

        if any(not re.fullmatch(r"[a-z0-9_]{1,64}", d) for d in v):
            raise ValueError("diseases must be lowercase snake_case identifiers")
        return v


class ScheduleOut(BaseModel):
    next_delivery_utc: datetime
    timezone: str
    local_time: str


class UnsubscribeIn(BaseModel):
    token: UUID


def _schedule(body: PreferencesIn) -> DeliverySchedule:
    try:
        hour, minute = (int(p) for p in body.delivery_time.split(":"))
        return DeliverySchedule(
            body.frequency, body.timezone, time(hour, minute), body.weekly_day, body.monthly_day
        )
    except (ValueError, ScheduleError) as exc:
        raise AppError(422, "invalid_schedule", str(exc)) from exc


def _repo(db: Database) -> SqlNotificationRepository:
    return SqlNotificationRepository(db)


@router.put("/notifications/preferences", response_model=ScheduleOut, summary="Save email settings")
async def put_preferences(
    body: PreferencesIn,
    user: Annotated[AuthenticatedUser, Depends(require_user)],
    db: Annotated[Database, Depends(get_db)],
) -> ScheduleOut:
    schedule = _schedule(body)
    repo = _repo(db)
    email, confirmed_at = await repo.auth_email(str(user.id))
    if email is None:
        raise AppError(404, "user_not_found", "Account not found.")
    if body.enabled and not body.consent:
        raise AppError(422, "consent_required", "Consent is required to enable email delivery.")
    if body.enabled and confirmed_at is None:
        raise AppError(409, "email_not_verified", "Verify your email address first.")
    now = datetime.now(UTC)
    await repo.upsert_preferences(
        **{
            "user_id": str(user.id),
            "email": email,
            "email_verified_at": confirmed_at.isoformat() if confirmed_at else None,
            "enabled": body.enabled,
            "digest_enabled": body.digest_enabled,
            "alerts_enabled": body.alerts_enabled,
            "frequency": body.frequency,
            "timezone": body.timezone,
            "delivery_time": body.delivery_time,
            "weekly_day": body.weekly_day,
            "monthly_day": body.monthly_day,
            "minimum_alert_severity": body.minimum_alert_severity,
            "selected_diseases": _json(body.selected_diseases),
            "consent": body.consent,
            "consent_version": CONSENT_TEXT_VERSION,
            "now": now.isoformat(),
        }
    )
    return ScheduleOut(
        next_delivery_utc=next_occurrence(schedule, now),
        timezone=body.timezone,
        local_time=body.delivery_time,
    )


def _json(values: list[str]) -> str:
    import json

    return json.dumps(values)


@router.post("/notifications/unsubscribe", status_code=204, summary="Unsubscribe by token")
async def unsubscribe(body: UnsubscribeIn, db: Annotated[Database, Depends(get_db)]) -> Response:
    """Always 204, so the endpoint cannot be used to discover which tokens exist."""
    await _repo(db).unsubscribe(str(body.token))
    return Response(status_code=204)


@router.post("/webhooks/resend", status_code=204, summary="Resend delivery events")
async def resend_webhook(
    request: Request,
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    body = await request.body()
    now = datetime.now(UTC)
    try:
        verify(
            settings.resend_webhook_secret.get_secret_value(),
            svix_id=request.headers.get("svix-id"),
            svix_timestamp=request.headers.get("svix-timestamp"),
            svix_signature=request.headers.get("svix-signature"),
            body=body,
            now=now,
        )
        event = parse_event(body)
    except WebhookVerificationError as exc:
        raise AppError(401, "invalid_webhook_signature", "Webhook rejected.") from exc
    except WebhookPayloadError as exc:
        raise AppError(400, "invalid_webhook_payload", "Webhook payload not understood.") from exc
    await _repo(db).record_event(request.headers["svix-id"], event, now)
    return Response(status_code=204)
