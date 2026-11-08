"""Connector registry. A source is available here only after its real format was verified."""

from __future__ import annotations

from collections.abc import Callable

from app.ingestion.connectors.base import SourceConnector
from app.ingestion.connectors.cdc_fluview import CdcFluViewConnector
from app.ingestion.connectors.flunet import FluNetConnector
from app.ingestion.connectors.nih_bulletin import NihBulletinConnector
from app.ingestion.errors import IngestionError

# Not registered on purpose (not implemented / not yet verified): cdc_nwss -- a real, public
# Socrata dataset was identified (see docs/source_notes_cdc_nwss.md) but its column schema was
# never confirmed from this sandbox, so no parser is implemented yet. Still seeded in
# data_sources but disabled.
_REGISTRY: dict[str, Callable[..., SourceConnector]] = {
    FluNetConnector.slug: FluNetConnector,
    CdcFluViewConnector.slug: CdcFluViewConnector,
    NihBulletinConnector.slug: NihBulletinConnector,
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
