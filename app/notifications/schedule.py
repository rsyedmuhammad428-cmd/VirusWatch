"""Timezone-aware digest schedules.

All arithmetic is done on local calendar dates and converted to UTC at the end, so daylight-saving
changes in a user's zone cannot shift a digest to the wrong day. Asia/Karachi has no DST (UTC+05:00
all year), but users may pick any IANA zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Frequency = Literal["daily", "weekly", "monthly"]
FREQUENCIES: tuple[Frequency, ...] = ("daily", "weekly", "monthly")

DEFAULT_TIMEZONE = "Asia/Karachi"
DEFAULT_TIME = time(9, 0)
DEFAULT_WEEKLY_DAY = 1  # ISO weekday, Monday
DEFAULT_MONTHLY_DAY = 1


class ScheduleError(ValueError):
    """The requested schedule is not valid."""


def load_timezone(name: str) -> ZoneInfo:
    # ZoneInfo accepts path-like keys on some platforms; allow only plain IANA identifiers.
    if not name or len(name) > 64 or name.startswith("/") or ".." in name or "\\" in name:
        raise ScheduleError(f"Unknown timezone {name!r}.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ScheduleError(f"Unknown timezone {name!r}.") from exc


@dataclass(frozen=True)
class DeliverySchedule:
    frequency: Frequency = "weekly"
    timezone: str = DEFAULT_TIMEZONE
    local_time: time = DEFAULT_TIME
    weekly_day: int = DEFAULT_WEEKLY_DAY  # ISO: 1 = Monday
    monthly_day: int = DEFAULT_MONTHLY_DAY

    def __post_init__(self) -> None:
        if self.frequency not in FREQUENCIES:
            raise ScheduleError(f"frequency must be one of {FREQUENCIES}.")
        load_timezone(self.timezone)
        if self.local_time.second or self.local_time.microsecond or self.local_time.tzinfo:
            raise ScheduleError("local_time must be a naive HH:MM time.")
        if not 1 <= self.weekly_day <= 7:
            raise ScheduleError("weekly_day must be 1 (Monday) to 7 (Sunday).")
        if not 1 <= self.monthly_day <= 28:
            raise ScheduleError("monthly_day must be 1 to 28.")

    @property
    def zone(self) -> ZoneInfo:
        return load_timezone(self.timezone)

    def matches(self, local_day: date) -> bool:
        if self.frequency == "daily":
            return True
        if self.frequency == "weekly":
            return local_day.isoweekday() == self.weekly_day
        return local_day.day == self.monthly_day


def occurrence_utc(schedule: DeliverySchedule, local_day: date) -> datetime:
    """The UTC instant of delivery on a local day.

    A wall time that does not exist (spring-forward gap) is interpreted with the pre-transition
    offset, i.e. it lands one gap-length later (02:30 becomes 03:30 local); a wall time that
    happens twice (autumn fold) uses the first occurrence.
    """
    zone = schedule.zone
    naive = datetime.combine(local_day, schedule.local_time)
    return naive.replace(tzinfo=zone, fold=0).astimezone(UTC)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleError("datetimes must be timezone-aware.")
    return value.astimezone(UTC)


def latest_due(
    schedule: DeliverySchedule,
    now: datetime,
    *,
    not_before: datetime | None,
    max_lateness: timedelta,
) -> datetime | None:
    """The most recent scheduled instant <= now that is still worth sending, else None.

    * Only the latest missed occurrence is considered: after downtime the user gets one digest,
      not a backlog.
    * Occurrences at or before `not_before` (consent time, last digest) are never sent.
    * An occurrence older than `max_lateness` is dropped: a stale "daily" digest is misleading.
    """
    now_utc = _require_aware_utc(now)
    floor = _require_aware_utc(not_before) if not_before else None
    local_today = now_utc.astimezone(schedule.zone).date()
    for back in range(0, 45):
        day = local_today - timedelta(days=back)
        if not schedule.matches(day):
            continue
        instant = occurrence_utc(schedule, day)
        if instant > now_utc:
            continue
        if floor is not None and instant <= floor:
            return None
        return instant if now_utc - instant <= max_lateness else None
    return None


def next_occurrence(schedule: DeliverySchedule, after: datetime) -> datetime:
    """First scheduled instant strictly after `after` (for showing the user their next digest)."""
    after_utc = _require_aware_utc(after)
    local_day = after_utc.astimezone(schedule.zone).date() - timedelta(days=1)
    for ahead in range(0, 70):
        day = local_day + timedelta(days=ahead)
        if schedule.matches(day):
            instant = occurrence_utc(schedule, day)
            if instant > after_utc:
                return instant
    raise ScheduleError("No occurrence found within 70 days.")  # unreachable for valid schedules


def period_key(schedule: DeliverySchedule, instant: datetime) -> str:
    """Stable identity of the delivery period; the heart of duplicate prevention."""
    local_day = _require_aware_utc(instant).astimezone(schedule.zone).date()
    if schedule.frequency == "daily":
        return f"daily:{local_day.isoformat()}"
    if schedule.frequency == "weekly":
        iso = local_day.isocalendar()
        return f"weekly:{iso.year}-W{iso.week:02d}"
    return f"monthly:{local_day.year}-{local_day.month:02d}"
