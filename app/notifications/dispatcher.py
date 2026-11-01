"""One scheduler cycle: plan -> queue -> claim -> render -> send -> record.

Delivery semantics (stated plainly, because they are a design choice):
  * Duplicate protection is enforced by the database (unique user + deduplication key), not by
    the scheduler's memory, so overlapping runs and restarts cannot double-queue.
  * A row is claimed (pending -> sending) before the provider is called. If the process dies
    after the provider accepted the message but before `sent` is recorded, the row stays in
    'sending' and is NOT retried automatically: at-most-once beats risking a duplicate email.
    `stuck_sending` exists so an operator can see such rows.
  * Transient provider errors (timeouts, 429, 5xx) go back to 'pending' with a back-off until
    max_attempts; permanent errors become 'failed'.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.notifications.digest import render
from app.notifications.planner import (
    DEFAULT_ACCEPTED_REVIEWS,
    plan_alert,
    plan_digest,
)
from app.notifications.ports import ClaimedEmail, NotificationRepository
from app.notifications.sender import EmailSender, EmailSendError, OutboundEmail

log = logging.getLogger(__name__)

PLACEHOLDER_SUBJECT = {"digest": "VirusWatch digest", "alert": "VirusWatch alert"}


@dataclass(frozen=True)
class DispatchConfig:
    batch_size: int = 100
    max_attempts: int = 3
    retry_backoff: timedelta = timedelta(minutes=5)
    alert_window: timedelta = timedelta(hours=24)
    stale_days: int = 28
    accepted_reviews: frozenset[str] = DEFAULT_ACCEPTED_REVIEWS
    frontend_base_url: str = ""


@dataclass
class CycleSummary:
    digests_queued: int = 0
    alerts_queued: int = 0
    duplicates_ignored: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def unsubscribe_url(base: str, token: str) -> str | None:
    return f"{base}/unsubscribe?token={token}" if base and token else None


async def run_cycle(
    repo: NotificationRepository,
    sender: EmailSender,
    *,
    now: datetime,
    config: DispatchConfig = DispatchConfig(),  # noqa: B008 - frozen dataclass, safe default
) -> CycleSummary:
    summary = CycleSummary()
    prefs = await repo.list_preferences()

    for pref in prefs:
        planned = plan_digest(pref, now)
        if planned is None:
            continue
        if await repo.enqueue(planned, PLACEHOLDER_SUBJECT["digest"]):
            summary.digests_queued += 1
        else:
            summary.duplicates_ignored += 1

    candidates = await repo.alert_candidates(now - config.alert_window, now)
    for candidate in candidates:
        for pref in prefs:
            planned = plan_alert(pref, candidate, now, config.accepted_reviews)
            if planned is None:
                continue
            if await repo.enqueue(planned, PLACEHOLDER_SUBJECT["alert"]):
                summary.alerts_queued += 1
            else:
                summary.duplicates_ignored += 1

    for claimed in await repo.claim_due(now, config.batch_size):
        await _deliver(repo, sender, claimed, now, config, summary)
    return summary


async def _deliver(
    repo: NotificationRepository,
    sender: EmailSender,
    claimed: ClaimedEmail,
    now: datetime,
    config: DispatchConfig,
    summary: CycleSummary,
) -> None:
    if not claimed.enabled:  # the user unsubscribed between queueing and sending
        await repo.mark_skipped(claimed, "user_disabled_notifications")
        summary.skipped += 1
        return
    try:
        if claimed.notification_type == "alert":
            items = await repo.alert_items(claimed)
        else:
            items = await repo.digest_items(claimed)
        url = unsubscribe_url(config.frontend_base_url, claimed.unsubscribe_token)
        subject, html, text = render(
            claimed.notification_type,
            items,
            now=now,
            timezone=claimed.timezone,
            unsubscribe_url=url,
        )
        message_id = await sender.send(OutboundEmail(claimed.email, subject, html, text, url))
    except EmailSendError as exc:
        status = await repo.mark_failed(
            claimed,
            str(exc),
            retryable=exc.retryable,
            retry_at=now + config.retry_backoff * claimed.attempt_count,
            max_attempts=config.max_attempts,
        )
        if status == "pending":
            summary.retried += 1
        else:
            summary.failed += 1
        log.warning("email_send_failed", extra={"retryable": exc.retryable, "status": status})
        return
    except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
        await repo.mark_failed(
            claimed,
            f"unexpected:{type(exc).__name__}",
            retryable=False,
            retry_at=now,
            max_attempts=config.max_attempts,
        )
        summary.failed += 1
        summary.errors.append(type(exc).__name__)
        log.exception("email_delivery_unexpected_error")
        return
    await repo.mark_sent(claimed, message_id, subject, now)
    await repo.apply_stored_events(message_id)
    if claimed.notification_type == "digest":
        await repo.touch_last_digest(claimed.user_id, now)
    summary.sent += 1
