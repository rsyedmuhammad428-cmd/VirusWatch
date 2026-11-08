"""Ports the dispatcher depends on (implemented by SQL in production, by fakes in tests)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.notifications.digest import DigestItem
from app.notifications.planner import AlertCandidate, PlannedEmail, Preference
from app.notifications.webhook import ResendEvent


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    notification_type: str  # 'digest' | 'alert' | 'test'
    subject: str
    status: str
    attempt_count: int
    scheduled_for: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    error_summary: str | None
    provider_message_id: str | None


@dataclass(frozen=True)
class ClaimedEmail:
    id: str
    user_id: str
    notification_type: str
    deduplication_key: str
    analysis_result_id: str | None
    attempt_count: int
    email: str
    timezone: str
    unsubscribe_token: str
    preferred_geography_id: str | None
    selected_diseases: tuple[str, ...]
    enabled: bool


class NotificationRepository(Protocol):
    async def list_preferences(self) -> list[Preference]: ...
    async def alert_candidates(self, since: datetime, today: datetime) -> list[AlertCandidate]: ...
    async def enqueue(self, planned: PlannedEmail, subject: str) -> bool:
        """True if a new row was queued, False if that period/analysis already exists."""
        ...

    async def claim_due(self, now: datetime, batch_size: int) -> list[ClaimedEmail]: ...
    async def digest_items(self, claimed: ClaimedEmail) -> list[DigestItem]: ...
    async def alert_items(self, claimed: ClaimedEmail) -> list[DigestItem]: ...
    async def mark_sent(
        self, claimed: ClaimedEmail, provider_message_id: str, subject: str, now: datetime
    ) -> None: ...
    async def mark_failed(
        self,
        claimed: ClaimedEmail,
        error: str,
        *,
        retryable: bool,
        retry_at: datetime,
        max_attempts: int,
    ) -> str: ...
    async def mark_skipped(self, claimed: ClaimedEmail, reason: str) -> None: ...
    async def touch_last_digest(self, user_id: str, at: datetime) -> None: ...
    async def apply_stored_events(self, provider_message_id: str) -> None:
        """Re-apply events that arrived before the send was recorded."""
        ...

    async def record_event(self, webhook_event_id: str, event: ResendEvent, now: datetime) -> bool:
        """Store the event (idempotent on its id) and advance the message's status.

        Returns False when this webhook delivery was already processed."""
        ...
