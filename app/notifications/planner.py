"""Decide which emails are due. Pure functions; no database, no network."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dtime

from app.notifications.schedule import (
    DeliverySchedule,
    Frequency,
    ScheduleError,
    latest_due,
    period_key,
)

# How late a digest may still go out after its scheduled instant (scheduler downtime tolerance).
MAX_LATENESS: dict[Frequency, timedelta] = {
    "daily": timedelta(hours=6),
    "weekly": timedelta(hours=24),
    "monthly": timedelta(hours=48),
}
SEVERITY_RANK = {"elevated": 1, "possible_anomaly": 2}
# Alerts need an explicit human approval by default. "auto" (computed, unreviewed) results are
# only accepted if the operator opts in via ALERT_ACCEPT_UNREVIEWED.
DEFAULT_ACCEPTED_REVIEWS = frozenset({"approved"})


@dataclass(frozen=True)
class Preference:
    """The columns of notification_preferences the scheduler needs."""

    user_id: str
    email: str
    enabled: bool
    digest_enabled: bool
    alerts_enabled: bool
    frequency: Frequency
    timezone: str
    local_time: dtime
    weekly_day: int
    monthly_day: int
    minimum_alert_severity: str
    consent_given_at: datetime | None
    email_verified_at: datetime | None
    last_digest_at: datetime | None
    selected_diseases: tuple[str, ...] = ()
    preferred_geography_id: str | None = None
    unsubscribe_token: str = ""

    @property
    def consented(self) -> bool:
        return (
            self.enabled
            and self.consent_given_at is not None
            and self.email_verified_at is not None
        )

    def schedule(self) -> DeliverySchedule:
        return DeliverySchedule(
            self.frequency, self.timezone, self.local_time, self.weekly_day, self.monthly_day
        )


@dataclass(frozen=True)
class PlannedEmail:
    user_id: str
    notification_type: str  # 'digest' | 'alert'
    deduplication_key: str
    scheduled_for: datetime
    analysis_result_id: str | None = None


def plan_digest(pref: Preference, now: datetime) -> PlannedEmail | None:
    if not (pref.consented and pref.digest_enabled):
        return None
    try:
        schedule = pref.schedule()
    except ScheduleError:
        return None  # a stored invalid schedule must not crash the whole run; it sends nothing
    floors = [t for t in (pref.consent_given_at, pref.last_digest_at) if t is not None]
    instant = latest_due(
        schedule,
        now,
        not_before=max(floors) if floors else None,
        max_lateness=MAX_LATENESS[schedule.frequency],
    )
    if instant is None:
        return None
    return PlannedEmail(
        user_id=pref.user_id,
        notification_type="digest",
        deduplication_key=f"digest:{pref.user_id}:{period_key(schedule, instant)}",
        scheduled_for=instant,
    )


@dataclass(frozen=True)
class AlertCandidate:
    """A stored analysis result, as read from analysis_results."""

    analysis_result_id: str
    status: str
    review_status: str
    disease: str
    geography_id: str
    indicator: str
    stale: bool
    small_sample: bool


def alert_decision(
    pref: Preference,
    candidate: AlertCandidate,
    accepted_reviews: frozenset[str] = DEFAULT_ACCEPTED_REVIEWS,
) -> tuple[bool, str]:
    """Whether to send an immediate alert, and the reason (also useful in logs and tests).

    "Validated high-priority anomaly" means ALL of: the user opted in to alerts; the result is at
    or above the user's minimum severity (only severities the method defines); a human or the
    review status is accepted (default: approved by a human); the data is not stale; and the
    latest observation is not flagged as a small sample.
    """
    if not pref.consented:
        return False, "no_consent_or_not_enabled"
    if not pref.alerts_enabled:
        return False, "alerts_disabled"
    rank = SEVERITY_RANK.get(candidate.status)
    if rank is None:
        return False, "status_not_alertable"
    if rank < SEVERITY_RANK.get(pref.minimum_alert_severity, 2):
        return False, "below_minimum_severity"
    if candidate.review_status not in accepted_reviews:
        return False, "not_validated"
    if candidate.stale:
        return False, "stale_data"
    if candidate.small_sample:
        return False, "small_sample"
    if pref.selected_diseases and candidate.disease not in pref.selected_diseases:
        return False, "disease_not_selected"
    if pref.preferred_geography_id and candidate.geography_id != pref.preferred_geography_id:
        return False, "geography_not_selected"
    return True, "eligible"


def plan_alert(
    pref: Preference,
    candidate: AlertCandidate,
    now: datetime,
    accepted_reviews: frozenset[str] = DEFAULT_ACCEPTED_REVIEWS,
) -> PlannedEmail | None:
    ok, _ = alert_decision(pref, candidate, accepted_reviews)
    if not ok:
        return None
    return PlannedEmail(
        user_id=pref.user_id,
        notification_type="alert",
        deduplication_key=f"alert:{candidate.analysis_result_id}",
        scheduled_for=now,
        analysis_result_id=candidate.analysis_result_id,
    )
