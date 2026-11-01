"""Plain, cautious wording for digests and alerts. Pure; HTML is escaped."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

STATUS_TEXT = {
    "normal": "No unusual change flagged",
    "elevated": "Elevated compared with its recent baseline",
    "possible_anomaly": "Possible statistical anomaly",
    "insufficient_data": "Not enough data to assess",
    "stale_data": "Data are out of date; no assessment made",
}
FOOTER = (
    "VirusWatch reports surveillance indicators and flags statistical changes. A flag is not a "
    "confirmed outbreak and this message is not medical advice. Check the official source before "
    "acting."
)


@dataclass(frozen=True)
class DigestItem:
    geography: str
    disease: str
    indicator: str
    stream: str | None
    status: str
    latest_observation_end: date | None
    observed_value: str | None
    baseline_value: str | None
    unit: str | None


def _line(item: DigestItem) -> str:
    stream = f" ({item.stream})" if item.stream else ""
    when = item.latest_observation_end.isoformat() if item.latest_observation_end else "n/a"
    values = ""
    if item.observed_value is not None and item.baseline_value is not None:
        unit = f" {item.unit}" if item.unit else ""
        values = f" Observed {item.observed_value}{unit} vs baseline {item.baseline_value}{unit}."
    status = STATUS_TEXT.get(item.status, item.status)
    return (
        f"{item.geography} - {item.disease.replace('_', ' ')} / "
        f"{item.indicator.replace('_', ' ')}{stream}: {status}. "
        f"Latest data week ends {when}.{values}"
    )


def render(
    kind: str, items: list[DigestItem], *, now: datetime, timezone: str, unsubscribe_url: str | None
) -> tuple[str, str, str]:
    """Return (subject, html, text)."""
    local = now.astimezone(ZoneInfo(timezone)).strftime("%d %b %Y")
    if kind == "alert":
        subject = "VirusWatch alert: statistical change flagged"
        heading = "A statistical change was flagged in reported surveillance data"
    else:
        subject = f"VirusWatch digest - {local}"
        heading = "Your VirusWatch surveillance digest"
    lines = [_line(i) for i in items] or ["No indicators match your selections right now."]
    text = "\n".join([heading, "", *[f"- {line}" for line in lines], "", FOOTER])
    if unsubscribe_url:
        text += f"\n\nUnsubscribe: {unsubscribe_url}"
    body = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    unsub = (
        f'<p><a href="{html.escape(unsubscribe_url, quote=True)}">Unsubscribe</a></p>'
        if unsubscribe_url
        else ""
    )
    page = (
        f"<h2>{html.escape(heading)}</h2><ul>{body}</ul>"
        f"<p><small>{html.escape(FOOTER)}</small></p>{unsub}"
    )
    return subject, page, text
