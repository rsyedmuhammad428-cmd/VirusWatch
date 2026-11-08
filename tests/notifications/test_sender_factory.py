"""choose_sender(): the single fail-closed policy shared by `app.cli notify` and the
POST /api/v1/me/notifications/test endpoint."""

from __future__ import annotations

import pytest

from app.notifications.sender import LoggingSender, ResendSender, choose_sender


def test_auto_mode_is_dev_outside_production():
    sender = choose_sender(
        api_key="key", from_address="a@b.test", environment="development", domain_verified=True
    )
    assert isinstance(sender, LoggingSender)


def test_auto_mode_is_dev_in_production_without_verified_domain():
    sender = choose_sender(
        api_key="key", from_address="a@b.test", environment="production", domain_verified=False
    )
    assert isinstance(sender, LoggingSender)


def test_auto_mode_is_live_only_in_production_with_verified_domain():
    sender = choose_sender(
        api_key="key", from_address="a@b.test", environment="production", domain_verified=True
    )
    assert isinstance(sender, ResendSender)


def test_live_mode_is_deliberate_override():
    sender = choose_sender(
        api_key="key",
        from_address="a@b.test",
        environment="development",
        domain_verified=False,
        mode="live",
    )
    assert isinstance(sender, ResendSender)


def test_live_mode_without_credentials_fails_closed():
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        choose_sender(
            api_key="",
            from_address="",
            environment="production",
            domain_verified=True,
            mode="live",
        )


def test_dev_mode_with_redirect_sends_through_resend_to_redirect_address():
    sender = choose_sender(
        api_key="key",
        from_address="a@b.test",
        environment="development",
        domain_verified=False,
        redirect_to="dev@example.test",
    )
    assert isinstance(sender, LoggingSender)
    assert sender._redirect_to == "dev@example.test"
    assert isinstance(sender._inner, ResendSender)


def test_dev_mode_with_redirect_but_no_credentials_fails_closed():
    with pytest.raises(ValueError, match="RESEND_API_KEY"):
        choose_sender(
            api_key="",
            from_address="",
            environment="development",
            domain_verified=False,
            redirect_to="dev@example.test",
        )


def test_dev_mode_without_redirect_suppresses_entirely_even_in_production():
    # production + no verified domain + no redirect: still dev, still safe.
    sender = choose_sender(
        api_key="key", from_address="a@b.test", environment="production", domain_verified=False
    )
    assert isinstance(sender, LoggingSender)
    assert sender._redirect_to is None
