"""Delivery-status state machine driven by Resend webhook events.

Webhooks are delivered at least once and may arrive out of order, so status only moves in allowed
directions: a late 'sent' never undoes 'delivered', and a negative outcome (bounce, complaint,
failure, suppression) is never overwritten by a later positive one.
"""

from __future__ import annotations

POSITIVE_ORDER = ["pending", "sending", "sent", "delivered"]
NEGATIVE = {"bounced", "complained", "failed", "suppressed"}

# Resend event type -> status. Unknown event types are stored but change nothing.
# 'email.delivery_delayed' is deliberately absent: a delay is not a final outcome.
EVENT_STATUS = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.failed": "failed",
    "email.suppressed": "suppressed",
}


def next_status(current: str, event_type: str) -> str:
    target = EVENT_STATUS.get(event_type)
    if target is None:
        return current
    if target in NEGATIVE:
        # A complaint may follow a delivery or a bounce record; other negatives do not stack.
        if current in NEGATIVE:
            return "complained" if (target == "complained" and current != "complained") else current
        return target
    if current in NEGATIVE or current in {"skipped"}:
        return current
    if current not in POSITIVE_ORDER:
        return current
    return target if POSITIVE_ORDER.index(target) > POSITIVE_ORDER.index(current) else current
