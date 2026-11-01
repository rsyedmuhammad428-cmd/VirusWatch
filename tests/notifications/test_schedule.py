from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from app.notifications.schedule import (
    DEFAULT_TIMEZONE,
    DeliverySchedule,
    ScheduleError,
    latest_due,
    load_timezone,
    next_occurrence,
    occurrence_utc,
    period_key,
)

LATE = timedelta(hours=24)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_defaults_are_9am_karachi_monday_first_of_month():
    s = DeliverySchedule()
    assert (s.timezone, s.local_time, s.weekly_day, s.monthly_day) == (
        "Asia/Karachi",
        time(9, 0),
        1,
        1,
    )
    assert DEFAULT_TIMEZONE == "Asia/Karachi"


def test_nine_am_karachi_is_04_00_utc():
    # Karachi is UTC+05:00 all year (no DST).
    assert occurrence_utc(DeliverySchedule("daily"), datetime(2026, 11, 1).date()) == utc(
        2026, 11, 1, 4, 0
    )
    assert occurrence_utc(DeliverySchedule("daily"), datetime(2026, 7, 1).date()) == utc(
        2026, 7, 1, 4, 0
    )


def test_weekly_is_monday_and_monthly_is_first():
    weekly = DeliverySchedule("weekly")
    assert next_occurrence(weekly, utc(2026, 10, 31, 12)) == utc(2026, 11, 2, 4)  # Mon 2 Nov
    monthly = DeliverySchedule("monthly")
    assert next_occurrence(monthly, utc(2026, 10, 31, 12)) == utc(2026, 11, 1, 4)
    assert next_occurrence(monthly, utc(2026, 11, 1, 4)) == utc(2026, 12, 1, 4)  # strictly after


def test_user_can_customise_time_day_and_timezone():
    s = DeliverySchedule("weekly", "America/Los_Angeles", time(7, 30), weekly_day=5)
    nxt = next_occurrence(s, utc(2026, 10, 31, 12))  # Fri 6 Nov, 07:30 PST (UTC-8 after DST ends)
    assert nxt == utc(2026, 11, 6, 15, 30)


def test_local_date_decides_the_day_not_utc():
    # 09:00 Karachi on Monday 2 Nov is still Monday in Karachi while it is Sunday 23:00 elsewhere.
    s = DeliverySchedule("weekly", "Pacific/Auckland", time(8, 0))
    assert s.matches(datetime(2026, 11, 2).date())
    assert occurrence_utc(s, datetime(2026, 11, 2).date()) == utc(2026, 11, 1, 19, 0)


def test_dst_spring_forward_gap_lands_one_hour_later_local():
    s = DeliverySchedule("daily", "America/New_York", time(2, 30))
    instant = occurrence_utc(s, datetime(2026, 3, 8).date())
    assert instant.astimezone(load_timezone("America/New_York")).strftime("%H:%M") == "03:30"


def test_dst_fold_uses_first_occurrence():
    s = DeliverySchedule("daily", "America/New_York", time(1, 30))
    instant = occurrence_utc(s, datetime(2026, 11, 1).date())
    assert instant == utc(2026, 11, 1, 5, 30)  # 01:30 EDT, the first of the two 01:30s


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timezone": "Mars/Olympus"},
        {"timezone": ""},
        {"timezone": "../etc/passwd"},
        {"weekly_day": 0},
        {"weekly_day": 8},
        {"monthly_day": 29},
        {"monthly_day": 0},
        {"local_time": time(9, 0, 30)},
        {"frequency": "hourly"},
    ],
)
def test_invalid_schedules_are_rejected(kwargs):
    with pytest.raises(ScheduleError):
        DeliverySchedule(**kwargs)


def test_latest_due_returns_only_the_latest_missed_occurrence_within_lateness():
    s = DeliverySchedule("daily")
    now = utc(2026, 11, 3, 4, 30)  # 30 minutes after 03 Nov's slot; 01 and 02 Nov were missed
    assert latest_due(s, now, not_before=None, max_lateness=LATE) == utc(2026, 11, 3, 4)


def test_too_late_occurrences_are_dropped():
    s = DeliverySchedule("daily")
    assert (
        latest_due(s, utc(2026, 11, 3, 20), not_before=None, max_lateness=timedelta(hours=6))
        is None
    )


def test_not_before_blocks_occurrences_already_sent_or_before_consent():
    s = DeliverySchedule("daily")
    now = utc(2026, 11, 3, 4, 5)
    assert latest_due(s, now, not_before=utc(2026, 11, 3, 4, 0, 30), max_lateness=LATE) is None
    assert latest_due(s, now, not_before=utc(2026, 11, 2, 12), max_lateness=LATE) == utc(
        2026, 11, 3, 4
    )


def test_nothing_due_before_the_slot():
    s = DeliverySchedule("daily")
    assert (
        latest_due(s, utc(2026, 11, 3, 3, 59), not_before=utc(2026, 11, 2, 12), max_lateness=LATE)
        is None
    )


def test_naive_datetimes_are_rejected():
    with pytest.raises(ScheduleError):
        latest_due(DeliverySchedule(), datetime(2026, 1, 1), not_before=None, max_lateness=LATE)


def test_period_keys_are_stable_per_period():
    daily, weekly, monthly = (DeliverySchedule(f) for f in ("daily", "weekly", "monthly"))
    t = utc(2026, 11, 2, 4)
    assert period_key(daily, t) == "daily:2026-11-02"
    assert period_key(weekly, t) == "weekly:2026-W45"
    assert period_key(monthly, t) == "monthly:2026-11"
    assert period_key(weekly, t + timedelta(minutes=3)) == period_key(weekly, t)
