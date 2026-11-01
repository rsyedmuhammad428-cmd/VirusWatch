"""Exception hierarchy for ingestion. Messages are safe to store in ingestion_runs.error_summary."""

from __future__ import annotations


class IngestionError(Exception):
    """Base class. The message must never contain secrets or full response bodies."""


class FetchError(IngestionError):
    """Permanent download failure (4xx other than 429, invalid content, size limit)."""


class TransientFetchError(FetchError):
    """Failure that is worth retrying (network error, timeout, 429, 5xx)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SnapshotValidationError(FetchError):
    """The download succeeded but is not the kind of file we asked for (e.g. an HTML error page)."""


class SchemaMismatchError(IngestionError):
    """The file's columns do not match what the parser requires. Nothing is persisted."""


class TooManyRejectionsError(IngestionError):
    """Too large a share of rows failed validation; the file is treated as untrustworthy."""


class PersistenceError(IngestionError):
    """The database write failed or an invariant about it was violated."""


class AlreadyRunningError(IngestionError):
    """Another live ingestion run exists for this source."""
