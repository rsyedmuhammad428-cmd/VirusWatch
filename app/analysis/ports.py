"""What the analysis service needs from storage (implemented by sql_repository.py)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.analysis.models import AnalysisResult, Point, SeriesKey


@dataclass(frozen=True)
class StoreOutcome:
    submitted: int
    inserted: int

    @property
    def already_stored(self) -> int:
        return self.submitted - self.inserted


class AnalysisRepository(Protocol):
    async def load_series(
        self, indicators: Sequence[str]
    ) -> list[tuple[SeriesKey, list[Point]]]: ...

    async def store_results(self, results: Sequence[AnalysisResult]) -> StoreOutcome: ...
