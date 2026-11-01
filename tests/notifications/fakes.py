"""In-memory NotificationRepository mirroring the semantics of app/notifications/sql.py
(verified separately against real PostgreSQL in tests/db/test_notifications_sql.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.notifications.digest import DigestItem
from app.notifications.planner import AlertCandidate, PlannedEmail, Preference
from app.notifications.ports import ClaimedEmail
from app.notifications.sender import EmailSendError, OutboundEmail
from app.notifications.status import next_status
from app.notifications.webhook import ResendEvent


@dataclass
class Row:
    id: str
    planned: PlannedEmail
    status: str = "pending"
    attempts: int = 0
    scheduled_for: datetime | None = None
    provider_id: str | None = None
    subject: str = ""
    error: str | None = None


@dataclass
class FakeRepo:
    prefs: list[Preference] = field(default_factory=list)
    candidates: list[AlertCandidate] = field(default_factory=list)
    rows: dict[tuple[str, str], Row] = field(default_factory=dict)
    events: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    last_digest: dict[str, datetime] = field(default_factory=dict)
    disabled_users: set[str] = field(default_factory=set)
    items: list[DigestItem] = field(default_factory=list)

    async def list_preferences(self):
        return [p for p in self.prefs if p.enabled]

    async def alert_candidates(self, since, today):
        return list(self.candidates)

    async def enqueue(self, planned, subject):
        key = (planned.user_id, planned.deduplication_key)
        if key in self.rows:
            return False
        self.rows[key] = Row(
            f"id{len(self.rows)}", planned, scheduled_for=planned.scheduled_for, subject=subject
        )
        return True

    async def claim_due(self, now, batch_size):
        out = []
        for row in self.rows.values():
            if row.status == "pending" and row.scheduled_for <= now and len(out) < batch_size:
                row.status, row.attempts = "sending", row.attempts + 1
                pref = next(p for p in self.prefs if p.user_id == row.planned.user_id)
                out.append(
                    ClaimedEmail(
                        row.id,
                        pref.user_id,
                        row.planned.notification_type,
                        row.planned.deduplication_key,
                        row.planned.analysis_result_id,
                        row.attempts,
                        pref.email,
                        pref.timezone,
                        pref.unsubscribe_token,
                        pref.preferred_geography_id,
                        pref.selected_diseases,
                        pref.user_id not in self.disabled_users,
                    )
                )
        return out

    def _row(self, claimed):
        return next(r for r in self.rows.values() if r.id == claimed.id)

    async def digest_items(self, claimed):
        return list(self.items)

    async def alert_items(self, claimed):
        return list(self.items)

    async def mark_sent(self, claimed, provider_message_id, subject, now):
        r = self._row(claimed)
        r.status, r.provider_id, r.subject = "sent", provider_message_id, subject

    async def mark_failed(self, claimed, error, *, retryable, retry_at, max_attempts):
        r = self._row(claimed)
        if retryable and r.attempts < max_attempts:
            r.status, r.scheduled_for = "pending", retry_at
        else:
            r.status = "failed"
        r.error = error
        return r.status

    async def mark_skipped(self, claimed, reason):
        self._row(claimed).status = "skipped"

    async def touch_last_digest(self, user_id, at):
        self.last_digest[user_id] = at

    async def apply_stored_events(self, provider_message_id):
        for r in self.rows.values():
            if r.provider_id == provider_message_id:
                for etype, mid in self.events.values():
                    if mid == provider_message_id:
                        r.status = next_status(r.status, etype)

    async def record_event(self, webhook_event_id, event: ResendEvent, now):
        if webhook_event_id in self.events:
            return False
        self.events[webhook_event_id] = (event.event_type, event.email_id)
        for r in self.rows.values():
            if r.provider_id and r.provider_id == event.email_id:
                r.status = next_status(r.status, event.event_type)
        return True


class FakeSender:
    def __init__(self, *script):
        self.script, self.sent = list(script), []

    async def send(self, message: OutboundEmail) -> str:
        item = self.script.pop(0) if self.script else None
        if isinstance(item, BaseException):
            raise item
        self.sent.append(message)
        return item or f"prov{len(self.sent)}"


def transient():
    return EmailSendError("provider_http_503", retryable=True)


def permanent():
    return EmailSendError("provider_http_422", retryable=False)
