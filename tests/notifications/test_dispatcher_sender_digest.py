from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.notifications.digest import DigestItem, render
from app.notifications.dispatcher import DispatchConfig, run_cycle
from app.notifications.planner import AlertCandidate, Preference
from app.notifications.sender import RESEND_URL, EmailSendError, OutboundEmail, ResendSender
from app.notifications.webhook import ResendEvent
from tests.notifications.fakes import FakeRepo, FakeSender, permanent, transient


def utc(*a):
    return datetime(*a, tzinfo=UTC)


MON = utc(2026, 11, 2, 4, 5)


def pref(uid="u1", **kw):
    base = dict(
        user_id=uid,
        email=f"{uid}@example.test",
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
        unsubscribe_token="tok-" + uid,
    )
    base.update(kw)
    return Preference(**base)


def candidate(**kw):
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


CFG = DispatchConfig(frontend_base_url="https://app.example.test")


async def test_digest_is_queued_sent_and_recorded_once():
    repo, sender = FakeRepo(prefs=[pref()]), FakeSender()
    s = await run_cycle(repo, sender, now=MON, config=CFG)
    assert (s.digests_queued, s.sent) == (1, 1)
    assert sender.sent[0].to == "u1@example.test"
    assert sender.sent[0].unsubscribe_url == "https://app.example.test/unsubscribe?token=tok-u1"
    assert repo.last_digest["u1"] == MON


async def test_running_the_cycle_again_never_sends_a_second_digest():
    repo, sender = FakeRepo(prefs=[pref()]), FakeSender()
    await run_cycle(repo, sender, now=MON, config=CFG)
    repo.last_digest.clear()  # even if last_digest_at were lost, the DB key still blocks it
    again = await run_cycle(repo, sender, now=MON + timedelta(minutes=10), config=CFG)
    assert (again.sent, again.duplicates_ignored) == (0, 1) and len(sender.sent) == 1


async def test_two_scheduler_instances_cannot_both_send():
    repo, sender = FakeRepo(prefs=[pref()]), FakeSender()
    a = await run_cycle(repo, sender, now=MON, config=CFG)
    b = await run_cycle(repo, sender, now=MON, config=CFG)
    assert a.sent + b.sent == 1


async def test_nothing_is_sent_outside_the_schedule():
    repo, sender = FakeRepo(prefs=[pref()]), FakeSender()
    s = await run_cycle(repo, sender, now=utc(2026, 11, 3, 4, 5), config=CFG)  # Tuesday
    assert s.sent == 0 and not repo.rows


async def test_alert_goes_out_immediately_once_per_analysis_result():
    repo = FakeRepo(prefs=[pref()], candidates=[candidate()])
    sender = FakeSender()
    tue = utc(2026, 11, 3, 10)  # not a digest day
    s1 = await run_cycle(repo, sender, now=tue, config=CFG)
    s2 = await run_cycle(repo, sender, now=tue + timedelta(minutes=5), config=CFG)
    assert (s1.alerts_queued, s1.sent, s2.sent, s2.duplicates_ignored) == (1, 1, 0, 1)
    assert "alert" in sender.sent[0].subject.lower()


async def test_unvalidated_stale_or_small_sample_results_never_alert():
    for c in (candidate(review_status="auto"), candidate(stale=True), candidate(small_sample=True)):
        repo, sender = FakeRepo(prefs=[pref()], candidates=[c]), FakeSender()
        s = await run_cycle(repo, sender, now=utc(2026, 11, 3, 10), config=CFG)
        assert s.sent == 0 and not repo.rows


async def test_alert_respects_each_users_opt_in():
    repo = FakeRepo(prefs=[pref("a"), pref("b", alerts_enabled=False)], candidates=[candidate()])
    s = await run_cycle(repo, FakeSender(), now=utc(2026, 11, 3, 10), config=CFG)
    assert s.alerts_queued == 1 and {k[0] for k in repo.rows} == {"a"}


async def test_transient_failure_is_retried_with_backoff_then_succeeds():
    repo, sender = FakeRepo(prefs=[pref()]), FakeSender(transient(), "ok1")
    s1 = await run_cycle(repo, sender, now=MON, config=CFG)
    row = next(iter(repo.rows.values()))
    assert (s1.retried, row.status, row.scheduled_for) == (1, "pending", MON + timedelta(minutes=5))
    s2 = await run_cycle(repo, sender, now=MON + timedelta(minutes=6), config=CFG)
    assert s2.sent == 1 and row.status == "sent" and row.attempts == 2


async def test_retries_stop_at_max_attempts():
    repo, sender = FakeRepo(prefs=[pref()]), FakeSender(transient(), transient(), transient())
    cfg = DispatchConfig(max_attempts=2)
    now = MON
    for _ in range(4):
        await run_cycle(repo, sender, now=now, config=cfg)
        now += timedelta(hours=1)
    row = next(iter(repo.rows.values()))
    assert row.status == "failed" and row.attempts == 2


async def test_permanent_error_fails_without_retry_and_other_users_still_get_mail():
    repo = FakeRepo(prefs=[pref("a"), pref("b")])
    s = await run_cycle(repo, FakeSender(permanent()), now=MON, config=CFG)
    assert (s.failed, s.sent, s.retried) == (1, 1, 0)


async def test_unexpected_exception_is_contained_and_recorded_without_leaking_details():
    repo, sender = FakeRepo(prefs=[pref("a"), pref("b")]), FakeSender(RuntimeError("secret 123"))
    s = await run_cycle(repo, sender, now=MON, config=CFG)
    assert (s.failed, s.sent) == (1, 1)
    assert all("secret" not in (r.error or "") for r in repo.rows.values())


async def test_user_who_unsubscribed_after_queueing_is_skipped_not_emailed():
    repo, sender = FakeRepo(prefs=[pref()], disabled_users={"u1"}), FakeSender()
    s = await run_cycle(repo, sender, now=MON, config=CFG)
    assert (s.skipped, s.sent) == (1, 0) and not sender.sent


async def test_webhook_events_move_status_idempotently_and_survive_out_of_order_delivery():
    repo = FakeRepo(prefs=[pref()])
    await run_cycle(repo, FakeSender("prov-x"), now=MON, config=CFG)
    row = next(iter(repo.rows.values()))
    assert await repo.record_event("w1", ResendEvent("email.delivered", "prov-x", {}), MON)
    assert row.status == "delivered"
    assert not await repo.record_event("w1", ResendEvent("email.delivered", "prov-x", {}), MON)
    await repo.record_event("w2", ResendEvent("email.sent", "prov-x", {}), MON)  # late
    assert row.status == "delivered"


async def test_event_that_arrives_before_the_send_is_recorded_is_applied_afterwards():
    repo = FakeRepo(prefs=[pref()])
    repo.events["w0"] = ("email.delivered", "prov-early")  # stored while no row had the id yet
    await run_cycle(repo, FakeSender("prov-early"), now=MON, config=CFG)
    assert next(iter(repo.rows.values())).status == "delivered"


async def test_poster_request_shape_and_error_mapping():
    calls = []

    def poster(status, payload):
        async def post(url, *, headers, json, timeout):
            calls.append((url, headers, json))
            return status, payload

        return post

    msg = OutboundEmail("to@example.test", "S", "<p>h</p>", "t", "https://x.test/u")
    ok = ResendSender("key", "VirusWatch <a@x.test>", poster(200, {"id": "abc"}))
    assert await ok.send(msg) == "abc"
    url, headers, body = calls[0]
    assert url == RESEND_URL and headers["Authorization"] == "Bearer key"
    assert (
        body["to"] == ["to@example.test"]
        and body["headers"]["List-Unsubscribe"] == "<https://x.test/u>"
    )
    for status, retryable in ((429, True), (503, True), (422, False), (401, False)):
        with pytest.raises(EmailSendError) as err:
            await ResendSender("k", "f", poster(status, {})).send(msg)
        assert err.value.retryable is retryable
    with pytest.raises(EmailSendError, match="without_id"):
        await ResendSender("k", "f", poster(200, {})).send(msg)


async def test_sender_requires_credentials():
    with pytest.raises(ValueError):
        ResendSender("", "from", None)  # type: ignore[arg-type]


def test_digest_text_is_escaped_cautious_and_never_claims_an_outbreak():
    item = DigestItem(
        "Pakistan <script>",
        "influenza",
        "influenza_positivity",
        "sentinel",
        "possible_anomaly",
        date(2026, 10, 18),
        "19.7",
        "6.1",
        "percent",
    )
    subject, html, text = render(
        "digest",
        [item],
        now=utc(2026, 11, 2, 4),
        timezone="Asia/Karachi",
        unsubscribe_url="https://x.test/u?t=1&b=2",
    )
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "not a confirmed outbreak" in text and "outbreak confirmed" not in text.lower()
    assert "Possible statistical anomaly" in text and "02 Nov 2026" in subject
    assert "Unsubscribe" in html and "&amp;" in html


def test_stale_or_empty_digest_says_so():
    _, _, text = render("digest", [], now=utc(2026, 11, 2), timezone="UTC", unsubscribe_url=None)
    assert "No indicators match" in text
