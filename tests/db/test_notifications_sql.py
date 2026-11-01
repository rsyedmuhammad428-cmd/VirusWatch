"""Migration 0010 and app/notifications/sql.py against a real PostgreSQL."""

from __future__ import annotations

import json
import uuid

import pytest

from app.notifications import sql
from tests.db.psql_harness import PsqlError

NOW = "2026-11-02T04:05:00+00:00"
LATER = "2026-11-02T05:00:00+00:00"


@pytest.fixture(autouse=True)
def clean_notifications(pg):
    pg.script(
        "truncate public.notification_history, public.email_events, "
        "public.notification_preferences cascade; delete from auth.users;"
    )


def user(pg, email="a@example.test", confirmed=True) -> str:
    uid = str(uuid.uuid4())
    pg.execute(
        "insert into auth.users (id, email, email_confirmed_at) "
        "values (cast(:id as uuid), :email, case when cast(:c as boolean) then now() end)",
        {"id": uid, "email": email, "c": confirmed},
    )
    return uid


def prefs(pg, uid, **overrides):
    values = dict(
        user_id=uid,
        email="a@example.test",
        email_verified_at=NOW,
        enabled=True,
        digest_enabled=True,
        alerts_enabled=False,
        frequency="weekly",
        timezone="Asia/Karachi",
        delivery_time="09:00",
        weekly_day=1,
        monthly_day=1,
        minimum_alert_severity="possible_anomaly",
        selected_diseases=json.dumps([]),
        consent=True,
        consent_version="v1",
        now=NOW,
    )
    values.update(overrides)
    return pg.execute(sql.UPSERT_PREFERENCES, values)


def enqueue(pg, uid, key="digest:u:weekly:2026-W45", when=NOW, kind="digest", analysis=None):
    return pg.execute(
        sql.ENQUEUE,
        {
            "user_id": uid,
            "notification_type": kind,
            "deduplication_key": key,
            "subject": "s",
            "scheduled_for": when,
            "analysis_result_id": analysis,
        },
    )


def status(pg, uid, key):
    return pg.scalar(
        "select status from public.notification_history where user_id = cast(:u as uuid) "
        "and deduplication_key = :k",
        {"u": uid, "k": key},
    )


def test_schedule_defaults_are_9am_karachi_monday_and_first(pg):
    uid = user(pg)
    pg.execute(
        "insert into public.notification_preferences (user_id, email) "
        "values (cast(:u as uuid), 'a@example.test')",
        {"u": uid},
    )
    row = pg.execute(
        "select timezone, delivery_time::text t, weekly_day, monthly_day, enabled "
        "from public.notification_preferences"
    )[0]
    assert (row["timezone"], row["t"], row["weekly_day"], row["monthly_day"]) == (
        "Asia/Karachi",
        "09:00:00",
        "1",
        "1",
    )
    assert row["enabled"] == "f"  # opt-in, never default-on


@pytest.mark.parametrize(
    "column,value",
    [
        ("weekly_day", 0),
        ("weekly_day", 8),
        ("monthly_day", 29),
        ("monthly_day", 0),
        ("delivery_time", "'09:00:30'"),
    ],
)
def test_invalid_schedule_values_are_rejected_by_the_database(pg, column, value):
    uid = user(pg)
    with pytest.raises(PsqlError):
        pg.execute(
            f"insert into public.notification_preferences (user_id, email, {column}) "  # noqa: S608
            f"values (cast(:u as uuid), 'a@example.test', {value})",
            {"u": uid},
        )


def test_status_vocabulary_is_pending_sending_sent_delivered_failed(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    assert status(pg, uid, "digest:u:weekly:2026-W45") == "pending"
    with pytest.raises(PsqlError):
        pg.execute("update public.notification_history set status = 'queued'")


def test_enqueue_is_the_duplicate_guard(pg):
    uid = user(pg)
    prefs(pg, uid)
    assert len(enqueue(pg, uid)) == 1
    assert enqueue(pg, uid) == []  # same period => nothing inserted
    assert pg.scalar("select count(*) from public.notification_history") == "1"
    assert len(enqueue(pg, uid, key="digest:u:weekly:2026-W46")) == 1  # next period is new


def test_claim_due_takes_each_row_once_and_only_when_due(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    enqueue(pg, uid, key="alert:later", when="2026-11-09T04:00:00+00:00", kind="alert")
    first = pg.execute(sql.CLAIM_DUE, {"now": NOW, "batch_size": 10})
    assert len(first) == 1 and first[0]["attempt_count"] == "1" and first[0]["enabled"] == "t"
    assert first[0]["email"] == "a@example.test"
    assert pg.execute(sql.CLAIM_DUE, {"now": NOW, "batch_size": 10}) == []  # no double claim
    assert status(pg, uid, "digest:u:weekly:2026-W45") == "sending"


def test_claim_respects_batch_size(pg):
    uid = user(pg)
    prefs(pg, uid)
    for i in range(3):
        enqueue(pg, uid, key=f"k{i}")
    assert len(pg.execute(sql.CLAIM_DUE, {"now": NOW, "batch_size": 2})) == 2


def test_sent_then_webhook_events_advance_status_and_delivery_time(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    claimed = pg.execute(sql.CLAIM_DUE, {"now": NOW, "batch_size": 1})[0]
    assert pg.execute(
        sql.MARK_SENT,
        {"id": claimed["id"], "provider_message_id": "prov1", "subject": "Hello", "now": NOW},
    )
    # MARK_SENT only works from 'sending'
    assert (
        pg.execute(
            sql.MARK_SENT,
            {"id": claimed["id"], "provider_message_id": "x", "subject": "Hello", "now": NOW},
        )
        == []
    )
    row = pg.execute(sql.FIND_FOR_UPDATE, {"provider_message_id": "prov1"})[0]
    pg.execute(
        sql.SET_STATUS,
        {"id": row["id"], "status": "delivered", "event_type": "email.delivered", "at": LATER},
    )
    done = pg.execute(
        "select status, delivered_at is not null d, last_event_type "
        "from public.notification_history"
    )[0]
    assert done == {"status": "delivered", "d": "t", "last_event_type": "email.delivered"}


def test_webhook_event_ids_are_idempotent(pg):
    args = {
        "webhook_event_id": "msg_1",
        "provider_message_id": "p",
        "event_type": "email.sent",
        "payload": {"type": "email.sent", "data": {}},
    }
    assert len(pg.execute(sql.INSERT_EVENT, args)) == 1
    assert pg.execute(sql.INSERT_EVENT, args) == []  # Resend retry
    ordered = pg.execute(sql.EVENTS_FOR_MESSAGE, {"provider_message_id": "p"})
    assert [e["event_type"] for e in ordered] == ["email.sent"]


def test_failures_retry_until_max_attempts_then_stay_failed(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    key = "digest:u:weekly:2026-W45"
    for attempt, expected in ((1, "pending"), (2, "pending"), (3, "failed")):
        claimed = pg.execute(sql.CLAIM_DUE, {"now": LATER, "batch_size": 1})
        assert claimed and claimed[0]["attempt_count"] == str(attempt)
        out = pg.execute(
            sql.MARK_FAILED,
            {
                "id": claimed[0]["id"],
                "error": "provider_http_503",
                "retryable": True,
                "retry_at": LATER,
                "max_attempts": 3,
            },
        )
        assert out[0]["status"] == expected
    assert status(pg, uid, key) == "failed"
    assert pg.execute(sql.CLAIM_DUE, {"now": LATER, "batch_size": 1}) == []


def test_permanent_failure_is_not_retried(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    claimed = pg.execute(sql.CLAIM_DUE, {"now": NOW, "batch_size": 1})[0]
    out = pg.execute(
        sql.MARK_FAILED,
        {
            "id": claimed["id"],
            "error": "e",
            "retryable": False,
            "retry_at": LATER,
            "max_attempts": 3,
        },
    )
    assert out[0]["status"] == "failed"


def test_stuck_sending_rows_are_visible_not_retried(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    pg.execute(sql.CLAIM_DUE, {"now": NOW, "batch_size": 1})
    assert len(pg.execute(sql.STUCK_SENDING, {"before": LATER})) == 1
    assert pg.execute(sql.CLAIM_DUE, {"now": LATER, "batch_size": 1}) == []


def test_upsert_writes_user_choices_and_keeps_first_consent(pg):
    uid = user(pg)
    prefs(
        pg,
        uid,
        timezone="America/Los_Angeles",
        delivery_time="07:30",
        weekly_day=5,
        selected_diseases=json.dumps(["dengue", "malaria"]),
    )
    # A later save re-sends consent (the API requires it whenever enabled); the original consent
    # time and text version are kept, not overwritten.
    prefs(
        pg,
        uid,
        consent=True,
        consent_version="v2",
        now=LATER,
        frequency="monthly",
        monthly_day=15,
        timezone="America/Los_Angeles",
        delivery_time="07:30",
        weekly_day=5,
    )
    row = pg.execute(
        "select digest_frequency f, monthly_day m, timezone tz, delivery_time::text t, "
        "consent_given_at = cast(:n as timestamptz) c, consent_text_version v "
        "from public.notification_preferences",
        {"n": NOW},
    )[0]
    assert row == {
        "f": "monthly",
        "m": "15",
        "tz": "America/Los_Angeles",
        "t": "07:30:00",
        "c": "t",
        "v": "v1",
    }


def test_enabling_without_consent_is_blocked_by_the_database(pg):
    uid = user(pg)
    with pytest.raises(PsqlError):
        prefs(pg, uid, consent=False)
    # ...but turning delivery off needs no consent flag, so users can always opt out.
    assert prefs(pg, uid, enabled=False, consent=False)


def test_unsubscribe_token_disables_and_pending_mail_is_flagged_disabled(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    token = pg.scalar("select unsubscribe_token::text from public.notification_preferences")
    assert len(pg.execute(sql.UNSUBSCRIBE, {"token": token})) == 1
    assert pg.execute(sql.UNSUBSCRIBE, {"token": str(uuid.uuid4())}) == []
    claimed = pg.execute(sql.CLAIM_DUE, {"now": NOW, "batch_size": 1})[0]
    assert claimed["enabled"] == "f"
    assert pg.execute(sql.LIST_PREFERENCES) == []


def test_auth_email_comes_from_auth_users(pg):
    uid = user(pg, "real@example.test", confirmed=False)
    row = pg.execute(sql.AUTH_EMAIL, {"user_id": uid})[0]
    assert row["email"] == "real@example.test" and row["confirmed_at"] == ""


def test_list_preferences_exposes_schedule_columns(pg):
    uid = user(pg)
    prefs(pg, uid, selected_diseases=json.dumps(["dengue"]))
    row = pg.execute(sql.LIST_PREFERENCES)[0]
    assert (row["local_time"], row["timezone"], row["frequency"]) == (
        "09:00",
        "Asia/Karachi",
        "weekly",
    )
    assert json.loads(row["selected_diseases"]) == ["dengue"]


def _geography(pg):
    return pg.scalar("select id from public.geographies where country_code = 'PK' limit 1")


def _analysis(pg, status="possible_anomaly", review="approved", end="2026-10-18", key="h1"):
    return pg.scalar(
        "insert into public.analysis_results (geography_id, disease, indicator, analysis_type, "
        "analysis_period_start, analysis_period_end, latest_observation_end, status, "
        "methodology_version, input_hash, review_status) values (cast(:g as uuid), 'influenza', "
        "'influenza_positivity', 'anomaly_detection', '2026-10-12', '2026-10-18', "
        "cast(:e as date), :s, '1.0.0', :h, :r) returning id::text",
        {"g": _geography(pg), "e": end, "s": status, "h": key, "r": review},
    )


def test_alert_candidates_report_review_state_and_staleness(pg):
    fresh = _analysis(pg, end="2026-10-18", key="a")
    old = _analysis(pg, end="2026-08-01", key="b", review="auto")
    _analysis(pg, status="normal", key="c")  # never an alert candidate
    rows = {
        r["analysis_result_id"]: r
        for r in pg.execute(
            sql.ALERT_CANDIDATES,
            {"since": "2020-01-01T00:00:00+00:00", "today": "2026-11-02", "stale_days": 28},
        )
    }
    assert set(rows) == {fresh, old}
    assert rows[fresh]["stale"] == "f" and rows[fresh]["review_status"] == "approved"
    assert rows[old]["stale"] == "t" and rows[old]["review_status"] == "auto"
    assert rows[fresh]["small_sample"] == "f"


def test_alert_item_and_digest_items_return_cautious_fields(pg):
    rid = _analysis(pg)
    item = pg.execute(sql.ALERT_ITEM, {"analysis_result_id": rid})[0]
    assert item["status"] == "possible_anomaly" and item["disease"] == "influenza"
    digest = pg.execute(sql.DIGEST_ITEMS, {"geography_id": None, "diseases": "[]"})
    assert len(digest) == 1
    assert pg.execute(sql.DIGEST_ITEMS, {"geography_id": None, "diseases": '["dengue"]'}) == []


def test_clients_cannot_read_or_write_delivery_tables(pg):
    uid = user(pg)
    prefs(pg, uid)
    enqueue(pg, uid)
    with pytest.raises(PsqlError):
        pg.execute("select * from public.email_events", role="anon")
    with pytest.raises(PsqlError):
        pg.execute("select * from public.notification_history", role="anon")
    with pytest.raises(PsqlError):
        pg.execute(
            "update public.notification_preferences set enabled = false", role="authenticated"
        )
