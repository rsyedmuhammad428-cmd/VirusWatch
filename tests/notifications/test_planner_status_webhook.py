from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta

import pytest

from app.notifications.planner import (
    AlertCandidate,
    Preference,
    alert_decision,
    plan_alert,
    plan_digest,
)
from app.notifications.status import next_status
from app.notifications.webhook import (
    WebhookPayloadError,
    WebhookVerificationError,
    parse_event,
    sign,
    verify,
)


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def pref(**kw) -> Preference:
    base = dict(
        user_id="u1",
        email="a@example.test",
        enabled=True,
        digest_enabled=True,
        alerts_enabled=True,
        frequency="weekly",
        timezone="Asia/Karachi",
        local_time=time(9, 0),
        weekly_day=1,
        monthly_day=1,
        minimum_alert_severity="possible_anomaly",
        consent_given_at=utc(2026, 10, 1),
        email_verified_at=utc(2026, 10, 1),
        last_digest_at=None,
    )
    base.update(kw)
    return Preference(**base)


MON_0905 = utc(2026, 11, 2, 4, 5)  # Monday 09:05 in Karachi


def test_weekly_digest_is_planned_monday_9am_karachi_with_period_key():
    plan = plan_digest(pref(), MON_0905)
    assert plan and plan.scheduled_for == utc(2026, 11, 2, 4)
    assert plan.deduplication_key == "digest:u1:weekly:2026-W45"


def test_same_period_always_yields_the_same_key_so_the_db_can_dedupe():
    a = plan_digest(pref(), MON_0905)
    b = plan_digest(pref(), MON_0905 + timedelta(hours=3))
    assert a and b and a.deduplication_key == b.deduplication_key


def test_not_planned_before_the_slot_or_on_other_weekdays():
    assert plan_digest(pref(), utc(2026, 11, 2, 3, 59)) is None
    assert plan_digest(pref(), utc(2026, 11, 3, 4, 5)) is None  # Tuesday


def test_last_digest_blocks_replanning():
    assert plan_digest(pref(last_digest_at=utc(2026, 11, 2, 4, 1)), MON_0905) is None


@pytest.mark.parametrize(
    "kw",
    [
        {"enabled": False},
        {"digest_enabled": False},
        {"consent_given_at": None},
        {"email_verified_at": None},
    ],
)
def test_no_digest_without_opt_in_consent_and_verification(kw):
    assert plan_digest(pref(**kw), MON_0905) is None


def test_consent_after_the_slot_does_not_trigger_a_catch_up_digest():
    assert plan_digest(pref(consent_given_at=utc(2026, 11, 2, 4, 2)), MON_0905) is None


def test_stored_invalid_schedule_sends_nothing_instead_of_crashing():
    assert plan_digest(pref(timezone="Nowhere/Land"), MON_0905) is None


def cand(**kw) -> AlertCandidate:
    base = dict(
        analysis_result_id="r1",
        status="possible_anomaly",
        review_status="approved",
        disease="influenza",
        geography_id="g1",
        indicator="influenza_positivity",
        stale=False,
        small_sample=False,
    )
    base.update(kw)
    return AlertCandidate(**base)


def test_validated_high_priority_alert_is_sent_with_result_based_key():
    plan = plan_alert(pref(), cand(), MON_0905)
    assert plan and plan.deduplication_key == "alert:r1" and plan.analysis_result_id == "r1"


@pytest.mark.parametrize(
    ("p", "c", "reason"),
    [
        ({"alerts_enabled": False}, {}, "alerts_disabled"),
        ({"enabled": False}, {}, "no_consent_or_not_enabled"),
        ({}, {"status": "normal"}, "status_not_alertable"),
        ({}, {"status": "stale_data"}, "status_not_alertable"),
        ({}, {"status": "elevated"}, "below_minimum_severity"),
        ({}, {"review_status": "auto"}, "not_validated"),
        ({}, {"review_status": "pending_review"}, "not_validated"),
        ({}, {"review_status": "rejected"}, "not_validated"),
        ({}, {"stale": True}, "stale_data"),
        ({}, {"small_sample": True}, "small_sample"),
        ({"selected_diseases": ("dengue",)}, {}, "disease_not_selected"),
        ({"preferred_geography_id": "other"}, {}, "geography_not_selected"),
    ],
)
def test_alert_gates(p, c, reason):
    assert alert_decision(pref(**p), cand(**c)) == (False, reason)


def test_elevated_alert_allowed_when_user_lowers_threshold_and_auto_when_operator_opts_in():
    assert alert_decision(pref(minimum_alert_severity="elevated"), cand(status="elevated"))[0]
    assert alert_decision(pref(), cand(review_status="auto"), frozenset({"approved", "auto"}))[0]


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        ("sending", "email.sent", "sent"),
        ("sent", "email.delivered", "delivered"),
        ("delivered", "email.sent", "delivered"),  # late event cannot move status backwards
        ("sent", "email.delivery_delayed", "sent"),
        ("sent", "email.bounced", "bounced"),
        ("delivered", "email.complained", "complained"),
        ("bounced", "email.delivered", "bounced"),  # negative outcome is never overwritten
        ("bounced", "email.failed", "bounced"),
        ("sent", "email.unknown_future_event", "sent"),
        ("skipped", "email.delivered", "skipped"),
    ],
)
def test_status_transitions(current, event, expected):
    assert next_status(current, event) == expected


SECRET = "whsec_" + "dGVzdC1zZWNyZXQtMTIzNDU2Nzg5MA=="


def signed(body: bytes, ts: datetime, svix_id="msg_1"):
    stamp = str(int(ts.timestamp()))
    return dict(
        svix_id=svix_id,
        svix_timestamp=stamp,
        svix_signature=sign(SECRET, svix_id, stamp, body),
        body=body,
    )


def test_valid_signature_passes_and_multiple_signatures_are_accepted():
    now = utc(2026, 11, 2, 4)
    kw = signed(b'{"type":"email.sent"}', now)
    verify(SECRET, now=now, **kw)
    kw["svix_signature"] = "v1,AAAA " + kw["svix_signature"]
    verify(SECRET, now=now, **kw)


@pytest.mark.parametrize(
    "mutate", ["body", "signature", "id", "secret", "old", "future", "missing"]
)
def test_forged_stale_or_incomplete_webhooks_are_rejected(mutate):
    now = utc(2026, 11, 2, 4)
    kw = signed(b'{"type":"email.sent"}', now)
    secret = SECRET
    if mutate == "body":
        kw["body"] = b'{"type":"email.delivered"}'
    elif mutate == "signature":
        kw["svix_signature"] = "v1,AAAAAAAA"
    elif mutate == "id":
        kw["svix_id"] = "msg_2"
    elif mutate == "secret":
        secret = "whsec_" + "b3RoZXItc2VjcmV0LTAwMDAwMDAwMDA="
    elif mutate == "old":
        kw = signed(b"{}", now - timedelta(minutes=6))
    elif mutate == "future":
        kw = signed(b"{}", now + timedelta(minutes=6))
    elif mutate == "missing":
        kw["svix_signature"] = None
    with pytest.raises(WebhookVerificationError):
        verify(secret, now=now, **kw)


def test_unconfigured_secret_rejects_everything():
    with pytest.raises(WebhookVerificationError):
        verify(
            "",
            svix_id="a",
            svix_timestamp="1",
            svix_signature="v1,x",
            body=b"",
            now=utc(2026, 1, 1),
        )


def test_parse_event_keeps_message_id_and_drops_recipient_addresses():
    body = json.dumps(
        {
            "type": "email.bounced",
            "created_at": "2026-11-02T04:00:00Z",
            "data": {
                "email_id": "e1",
                "to": ["secret@example.test"],
                "bounce": {"type": "Permanent"},
            },
        }
    ).encode()
    ev = parse_event(body)
    assert ev.email_id == "e1" and ev.event_type == "email.bounced"
    assert "secret@example.test" not in json.dumps(ev.payload)
    assert ev.payload["data"]["bounce"] == {"type": "Permanent"}


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"data":{}}'])
def test_unusable_payloads_raise(body):
    with pytest.raises(WebhookPayloadError):
        parse_event(body)
