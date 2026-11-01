"""Connector registry. A source is available here only after its real format was verified."""

from __future__ import annotations

from collections.abc import Callable

from app.ingestion.connectors.base import SourceConnector
from app.ingestion.connectors.flunet import FluNetConnector
from app.ingestion.errors import IngestionError

# Not registered on purpose (not implemented / not yet verified): pk_nih_weekly_bulletin,
# cdc_fluview, cdc_nwss. They are seeded in data_sources but disabled.
_REGISTRY: dict[str, Callable[..., SourceConnector]] = {
    FluNetConnector.slug: FluNetConnector,
}


def available_sources() -> list[str]:
    return sorted(_REGISTRY)


def get_connector(slug: str, **kwargs: object) -> SourceConnector:
    try:
        factory = _REGISTRY[slug]
    except KeyError:
        raise IngestionError(
            f"No connector for source {slug!r}. Available: {', '.join(available_sources())}."
        ) from None
    return factory(**kwargs)
