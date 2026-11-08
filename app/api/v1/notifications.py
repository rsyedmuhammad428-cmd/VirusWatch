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
from app.notifications.sender import OutboundEmail, choose_sender
from app.notifications.sql_repository import SqlNotificationRepository
from app.notifications.webhook import (
    WebhookPayloadError,
    WebhookVerificationError,
    parse_event,
    verify,
)

TEST_SUBJECT = "VirusWatch - test email"
TEST_TEXT = (
    "This is a test email from VirusWatch to confirm your notification settings are working.\n\n"
    "It is not a surveillance digest or alert and contains no disease data. If your scheduled "
    "digests or alerts are enabled, they will arrive separately on your chosen schedule."
)
TEST_HTML = (
    "<p>This is a test email from VirusWatch to confirm your notification settings are "
    "working.</p><p>It is not a surveillance digest or alert and contains no disease data. "
    "If your scheduled digests or alerts are enabled, they will arrive separately on your "
    "chosen schedule.</p>"
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


@router.put(
    "/me/notifications/preferences", response_model=ScheduleOut, summary="Save email settings"
)
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


class PreferencesOut(BaseModel):
    enabled: bool
    digest_enabled: bool
    alerts_enabled: bool
    frequency: str
    timezone: str
    local_time: str
    weekly_day: int
    monthly_day: int
    minimum_alert_severity: str
    selected_diseases: list[str]
    email_verified: bool


class HistoryOut(BaseModel):
    id: str
    notification_type: str
    subject: str
    status: str
    attempt_count: int
    scheduled_for: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    error_summary: str | None


class MyNotificationsOut(BaseModel):
    preferences: PreferencesOut | None
    recent_history: list[HistoryOut]


class TestSendOut(BaseModel):
    status: Literal["sent"]
    mode: Literal["live", "dev_redirected", "dev_suppressed"]
    detail: str


@router.get("/me/notifications", response_model=MyNotificationsOut, summary="My notifications")
async def get_my_notifications(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
    db: Annotated[Database, Depends(get_db)],
) -> MyNotificationsOut:
    """A user's own settings (even while disabled) plus their recent delivery history. Never
    returns an email's subject body or HTML/text content, only metadata about attempts."""
    repo = _repo(db)
    pref = await repo.get_preferences(str(user.id))
    history = await repo.history(str(user.id))
    preferences_out = (
        None
        if pref is None
        else PreferencesOut(
            enabled=pref.enabled,
            digest_enabled=pref.digest_enabled,
            alerts_enabled=pref.alerts_enabled,
            frequency=pref.frequency,
            timezone=pref.timezone,
            local_time=pref.local_time.strftime("%H:%M"),
            weekly_day=pref.weekly_day,
            monthly_day=pref.monthly_day,
            minimum_alert_severity=pref.minimum_alert_severity,
            selected_diseases=list(pref.selected_diseases),
            email_verified=pref.email_verified_at is not None,
        )
    )
    return MyNotificationsOut(
        preferences=preferences_out,
        recent_history=[
            HistoryOut(
                id=h.id,
                notification_type=h.notification_type,
                subject=h.subject,
                status=h.status,
                attempt_count=h.attempt_count,
                scheduled_for=h.scheduled_for,
                sent_at=h.sent_at,
                delivered_at=h.delivered_at,
                error_summary=h.error_summary,
            )
            for h in history
        ],
    )


@router.post(
    "/me/notifications/test",
    response_model=TestSendOut,
    summary="Send a safe test email (no surveillance data)",
)
async def send_test_notification(
    user: Annotated[AuthenticatedUser, Depends(require_user)],
    db: Annotated[Database, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TestSendOut:
    """Proves the preferences -> sender -> delivery-tracking pipeline is wired correctly.
    Deliberately carries no real surveillance figures, so this endpoint can never be the way a
    public-health claim reaches a user by accident; it only confirms delivery mechanics."""
    repo = _repo(db)
    email, confirmed_at = await repo.auth_email(str(user.id))
    if email is None:
        raise AppError(404, "user_not_found", "Account not found.")
    if confirmed_at is None:
        raise AppError(409, "email_not_verified", "Verify your email address first.")
    try:
        sender = choose_sender(
            api_key=settings.resend_api_key.get_secret_value(),
            from_address=settings.email_from,
            environment=settings.environment,
            domain_verified=settings.resend_domain_verified,
        )
    except ValueError as exc:
        raise AppError(503, "sender_not_configured", str(exc)) from exc
    now = datetime.now(UTC)
    message = OutboundEmail(to=email, subject=TEST_SUBJECT, html=TEST_HTML, text=TEST_TEXT)
    succeeded = True
    provider_message_id: str | None = None
    error_summary: str | None = None
    try:
        provider_message_id = await sender.send(message)
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised as an AppError
        succeeded = False
        error_summary = str(exc)[:500]
    await repo.record_test_send(
        user_id=str(user.id),
        subject=TEST_SUBJECT,
        succeeded=succeeded,
        provider_message_id=provider_message_id,
        error_summary=error_summary,
        now=now,
    )
    if not succeeded:
        raise AppError(502, "test_send_failed", error_summary or "Test send failed.")
    mode: Literal["live", "dev_redirected", "dev_suppressed"]
    if settings.environment == "production" and settings.resend_domain_verified:
        mode = "live"
    elif (provider_message_id or "").startswith("dev-"):
        mode = "dev_suppressed"
    else:
        mode = "dev_redirected"
    return TestSendOut(status="sent", mode=mode, detail=f"Test email sent to {email}.")


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
