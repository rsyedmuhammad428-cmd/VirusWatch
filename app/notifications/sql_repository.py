"""PostgreSQL implementation of NotificationRepository (SQLAlchemy Core + asyncpg)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import text

from app.db import Database
from app.notifications import sql
from app.notifications.digest import DigestItem
from app.notifications.planner import AlertCandidate, PlannedEmail, Preference
from app.notifications.ports import ClaimedEmail, HistoryEntry
from app.notifications.status import next_status
from app.notifications.webhook import ResendEvent


def _json_list(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    return tuple(json.loads(raw) if isinstance(raw, str) else raw)


def _hhmm(raw: Any) -> time:
    if isinstance(raw, time):
        return raw
    hour, minute = str(raw).split(":")[:2]
    return time(int(hour), int(minute))


def _dt(raw: Any) -> datetime | None:
    if raw is None or isinstance(raw, datetime):
        return raw
    return datetime.fromisoformat(str(raw))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _item(row: Any) -> DigestItem:
    end = row["latest_observation_end"]
    if isinstance(end, str):
        end = date.fromisoformat(end)
    return DigestItem(
        geography=row["geography"],
        disease=row["disease"],
        indicator=row["indicator"],
        stream=row["stream"],
        status=row["status"],
        latest_observation_end=end,
        observed_value=row["observed_value"],
        baseline_value=row["baseline_value"],
        unit=row["unit"],
    )


class SqlNotificationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_preferences(self) -> list[Preference]:
        async with self._db.session(read_only=True) as conn:
            rows = (await conn.execute(text(sql.LIST_PREFERENCES))).mappings().all()
        return [
            Preference(
                user_id=r["user_id"],
                email=r["email"],
                enabled=r["enabled"],
                digest_enabled=r["digest_enabled"],
                alerts_enabled=r["alerts_enabled"],
                frequency=r["frequency"],
                timezone=r["timezone"],
                local_time=_hhmm(r["local_time"]),
                weekly_day=int(r["weekly_day"]),
                monthly_day=int(r["monthly_day"]),
                minimum_alert_severity=r["minimum_alert_severity"],
                consent_given_at=_dt(r["consent_given_at"]),
                email_verified_at=_dt(r["email_verified_at"]),
                last_digest_at=_dt(r["last_digest_at"]),
                selected_diseases=_json_list(r["selected_diseases"]),
                preferred_geography_id=r["preferred_geography_id"],
                unsubscribe_token=r["unsubscribe_token"],
            )
            for r in rows
        ]

    async def alert_candidates(self, since: datetime, today: datetime) -> list[AlertCandidate]:
        async with self._db.session(read_only=True) as conn:
            rows = (
                (
                    await conn.execute(
                        text(sql.ALERT_CANDIDATES),
                        {
                            "since": _iso(since),
                            "today": today.astimezone(UTC).date().isoformat(),
                            "stale_days": 28,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [
            AlertCandidate(
                analysis_result_id=r["analysis_result_id"],
                status=r["status"],
                review_status=r["review_status"],
                disease=r["disease"],
                geography_id=r["geography_id"],
                indicator=r["indicator"],
                stale=bool(r["stale"]),
                small_sample=bool(r["small_sample"]),
            )
            for r in rows
        ]

    async def enqueue(self, planned: PlannedEmail, subject: str) -> bool:
        async with self._db.session() as conn:
            row = (
                await conn.execute(
                    text(sql.ENQUEUE),
                    {
                        "user_id": planned.user_id,
                        "notification_type": planned.notification_type,
                        "deduplication_key": planned.deduplication_key,
                        "subject": subject,
                        "scheduled_for": _iso(planned.scheduled_for),
                        "analysis_result_id": planned.analysis_result_id,
                    },
                )
            ).first()
        return row is not None

    async def claim_due(self, now: datetime, batch_size: int) -> list[ClaimedEmail]:
        async with self._db.session() as conn:
            rows = (
                (
                    await conn.execute(
                        text(sql.CLAIM_DUE), {"now": _iso(now), "batch_size": batch_size}
                    )
                )
                .mappings()
                .all()
            )
        return [
            ClaimedEmail(
                id=r["id"],
                user_id=r["user_id"],
                notification_type=r["notification_type"],
                deduplication_key=r["deduplication_key"],
                analysis_result_id=r["analysis_result_id"],
                attempt_count=int(r["attempt_count"]),
                email=r["email"],
                timezone=r["timezone"],
                unsubscribe_token=r["unsubscribe_token"],
                preferred_geography_id=r["preferred_geography_id"],
                selected_diseases=_json_list(r["selected_diseases"]),
                enabled=bool(r["enabled"]),
            )
            for r in rows
        ]

    async def digest_items(self, claimed: ClaimedEmail) -> list[DigestItem]:
        async with self._db.session(read_only=True) as conn:
            rows = (
                (
                    await conn.execute(
                        text(sql.DIGEST_ITEMS),
                        {
                            "geography_id": claimed.preferred_geography_id,
                            "diseases": json.dumps(list(claimed.selected_diseases)),
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [_item(r) for r in rows]

    async def alert_items(self, claimed: ClaimedEmail) -> list[DigestItem]:
        async with self._db.session(read_only=True) as conn:
            rows = (
                (
                    await conn.execute(
                        text(sql.ALERT_ITEM), {"analysis_result_id": claimed.analysis_result_id}
                    )
                )
                .mappings()
                .all()
            )
        return [_item(r) for r in rows]

    async def mark_sent(
        self, claimed: ClaimedEmail, provider_message_id: str, subject: str, now: datetime
    ) -> None:
        async with self._db.session() as conn:
            await conn.execute(
                text(sql.MARK_SENT),
                {
                    "id": claimed.id,
                    "provider_message_id": provider_message_id,
                    "subject": subject,
                    "now": _iso(now),
                },
            )

    async def mark_failed(
        self,
        claimed: ClaimedEmail,
        error: str,
        *,
        retryable: bool,
        retry_at: datetime,
        max_attempts: int,
    ) -> str:
        async with self._db.session() as conn:
            row = (
                await conn.execute(
                    text(sql.MARK_FAILED),
                    {
                        "id": claimed.id,
                        "error": error,
                        "retryable": retryable,
                        "retry_at": _iso(retry_at),
                        "max_attempts": max_attempts,
                    },
                )
            ).first()
        return str(row.status) if row else "failed"

    async def mark_skipped(self, claimed: ClaimedEmail, reason: str) -> None:
        async with self._db.session() as conn:
            await conn.execute(text(sql.MARK_SKIPPED), {"id": claimed.id, "reason": reason})

    async def touch_last_digest(self, user_id: str, at: datetime) -> None:
        async with self._db.session() as conn:
            await conn.execute(text(sql.TOUCH_LAST_DIGEST), {"user_id": user_id, "at": _iso(at)})

    async def apply_stored_events(self, provider_message_id: str) -> None:
        async with self._db.session() as conn:
            events = (
                (
                    await conn.execute(
                        text(sql.EVENTS_FOR_MESSAGE), {"provider_message_id": provider_message_id}
                    )
                )
                .mappings()
                .all()
            )
            row = (
                await conn.execute(
                    text(sql.FIND_FOR_UPDATE), {"provider_message_id": provider_message_id}
                )
            ).first()
            if row is None:
                return
            status = str(row.status)
            for event in events:
                new = next_status(status, event["event_type"])
                if new != status:
                    at = _dt(event["received_at"]) or datetime.now(UTC)
                    await conn.execute(
                        text(sql.SET_STATUS),
                        {
                            "id": row.id,
                            "status": new,
                            "event_type": event["event_type"],
                            "at": _iso(at),
                        },
                    )
                    status = new

    async def record_event(self, webhook_event_id: str, event: ResendEvent, now: datetime) -> bool:
        async with self._db.session() as conn:
            inserted = (
                await conn.execute(
                    text(sql.INSERT_EVENT),
                    {
                        "webhook_event_id": webhook_event_id,
                        "provider_message_id": event.email_id,
                        "event_type": event.event_type,
                        "payload": json.dumps(event.payload),
                    },
                )
            ).first()
            if inserted is None:
                return False  # already processed: Resend retries deliveries
            if event.email_id is None:
                return True
            row = (
                await conn.execute(
                    text(sql.FIND_FOR_UPDATE), {"provider_message_id": event.email_id}
                )
            ).first()
            if row is not None:
                new = next_status(str(row.status), event.event_type)
                if new != str(row.status):
                    await conn.execute(
                        text(sql.SET_STATUS),
                        {
                            "id": row.id,
                            "status": new,
                            "event_type": event.event_type,
                            "at": _iso(now),
                        },
                    )
        return True

    async def auth_email(self, user_id: str) -> tuple[str | None, datetime | None]:
        async with self._db.session(read_only=True) as conn:
            row = (await conn.execute(text(sql.AUTH_EMAIL), {"user_id": user_id})).first()
        return (None, None) if row is None else (row.email, _dt(row.confirmed_at))

    async def upsert_preferences(
        self,
        *,
        user_id: str,
        email: str,
        email_verified_at: str | None,
        enabled: bool,
        digest_enabled: bool,
        alerts_enabled: bool,
        frequency: str,
        timezone: str,
        delivery_time: str,
        weekly_day: int,
        monthly_day: int,
        minimum_alert_severity: str,
        selected_diseases: str,
        consent: bool,
        consent_version: str,
        now: str,
    ) -> None:
        async with self._db.session() as conn:
            await conn.execute(
                text(sql.UPSERT_PREFERENCES),
                {
                    "user_id": user_id,
                    "email": email,
                    "email_verified_at": email_verified_at,
                    "enabled": enabled,
                    "digest_enabled": digest_enabled,
                    "alerts_enabled": alerts_enabled,
                    "frequency": frequency,
                    "timezone": timezone,
                    "delivery_time": delivery_time,
                    "weekly_day": weekly_day,
                    "monthly_day": monthly_day,
                    "minimum_alert_severity": minimum_alert_severity,
                    "selected_diseases": selected_diseases,
                    "consent": consent,
                    "consent_version": consent_version,
                    "now": now,
                },
            )

    async def stuck_sending(self, before: datetime) -> list[str]:
        """Ids stuck in 'sending' (process died mid-send); reported, never auto-retried."""
        async with self._db.session(read_only=True) as conn:
            rows = (await conn.execute(text(sql.STUCK_SENDING), {"before": _iso(before)})).all()
        return [r.id for r in rows]

    async def unsubscribe(self, token: str) -> bool:
        async with self._db.session() as conn:
            row = (await conn.execute(text(sql.UNSUBSCRIBE), {"token": token})).first()
        return row is not None

    async def get_preferences(self, user_id: str) -> Preference | None:
        async with self._db.session(read_only=True) as conn:
            row = (
                (await conn.execute(text(sql.GET_PREFERENCES), {"user_id": user_id}))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return Preference(
            user_id=row["user_id"],
            email=row["email"],
            enabled=row["enabled"],
            digest_enabled=row["digest_enabled"],
            alerts_enabled=row["alerts_enabled"],
            frequency=row["frequency"],
            timezone=row["timezone"],
            local_time=_hhmm(row["local_time"]),
            weekly_day=int(row["weekly_day"]),
            monthly_day=int(row["monthly_day"]),
            minimum_alert_severity=row["minimum_alert_severity"],
            consent_given_at=_dt(row["consent_given_at"]),
            email_verified_at=_dt(row["email_verified_at"]),
            last_digest_at=_dt(row["last_digest_at"]),
            selected_diseases=_json_list(row["selected_diseases"]),
            preferred_geography_id=row["preferred_geography_id"],
            unsubscribe_token=row["unsubscribe_token"],
        )

    async def history(self, user_id: str, limit: int = 20) -> list[HistoryEntry]:
        async with self._db.session(read_only=True) as conn:
            rows = (
                (await conn.execute(text(sql.HISTORY), {"user_id": user_id, "limit": limit}))
                .mappings()
                .all()
            )
        return [
            HistoryEntry(
                id=r["id"],
                notification_type=r["notification_type"],
                subject=r["subject"],
                status=r["status"],
                attempt_count=int(r["attempt_count"]),
                scheduled_for=_dt(r["scheduled_for"]),
                sent_at=_dt(r["sent_at"]),
                delivered_at=_dt(r["delivered_at"]),
                error_summary=r["error_summary"],
                provider_message_id=r["provider_message_id"],
            )
            for r in rows
        ]

    async def record_test_send(
        self,
        *,
        user_id: str,
        subject: str,
        succeeded: bool,
        provider_message_id: str | None,
        error_summary: str | None,
        now: datetime,
    ) -> str:
        key = f"test:{user_id}:{now.isoformat()}"
        async with self._db.session() as conn:
            row = (
                await conn.execute(
                    text(sql.INSERT_TEST_SEND),
                    {
                        "user_id": user_id,
                        "deduplication_key": key,
                        "subject": subject,
                        "succeeded": succeeded,
                        "now": _iso(now),
                        "provider_message_id": provider_message_id,
                        "error_summary": error_summary,
                    },
                )
            ).first()
        return row.id
