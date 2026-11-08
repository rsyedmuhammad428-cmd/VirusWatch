from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ingestion.connectors.cdc_fluview import (
    ILINET_ENTRY,
    NREVSS_CLINICAL_ENTRY,
    NREVSS_PUBLIC_HEALTH_ENTRY,
    CdcFluViewConnector,
    bundle_bytes,
)
from app.ingestion.errors import FetchError
from app.ingestion.models import IngestRequest

US = frozenset({"US"})
REQUEST = IngestRequest(countries=US, since_year=None)
REAL_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "cdc_fluview"


def run(coro):
    return asyncio.run(coro)


def _write_fixture_set(tmp_path: Path) -> Path:
    (tmp_path / ILINET_ENTRY).write_bytes(b"ILINET DATA\nREGION TYPE,REGION\nNational,X\n")
    (tmp_path / NREVSS_CLINICAL_ENTRY).write_bytes(b"NREVSS\nREGION TYPE,REGION\nNational,X\n")
    (tmp_path / NREVSS_PUBLIC_HEALTH_ENTRY).write_bytes(b"NREVSS\nREGION TYPE,REGION\nNational,X\n")
    return tmp_path


class TestBundleBytes:
    def test_deterministic_for_identical_input(self):
        files = {
            ILINET_ENTRY: b"a",
            NREVSS_CLINICAL_ENTRY: b"b",
            NREVSS_PUBLIC_HEALTH_ENTRY: b"c",
        }
        assert bundle_bytes(files) == bundle_bytes(files)

    def test_different_content_changes_the_bundle(self):
        files_a = {
            ILINET_ENTRY: b"a",
            NREVSS_CLINICAL_ENTRY: b"b",
            NREVSS_PUBLIC_HEALTH_ENTRY: b"c",
        }
        files_b = dict(files_a, **{ILINET_ENTRY: b"a-changed"})
        assert bundle_bytes(files_a) != bundle_bytes(files_b)


class TestFetch:
    def test_missing_files_raise_fetch_error_naming_the_download_page(self, tmp_path):
        conn = CdcFluViewConnector(data_dir=tmp_path)
        with pytest.raises(FetchError) as exc:
            run(conn.fetch(REQUEST, fetcher=None))
        assert "cdc.gov/fluview" in str(exc.value)
        assert ILINET_ENTRY in str(exc.value)

    def test_partial_files_present_still_raises(self, tmp_path):
        (tmp_path / ILINET_ENTRY).write_bytes(b"x")
        conn = CdcFluViewConnector(data_dir=tmp_path)
        with pytest.raises(FetchError) as exc:
            run(conn.fetch(REQUEST, fetcher=None))
        assert NREVSS_CLINICAL_ENTRY in str(exc.value)
        assert ILINET_ENTRY not in str(exc.value)

    def test_snapshot_checksum_is_sha256_of_its_own_content(self, tmp_path):
        import hashlib

        _write_fixture_set(tmp_path)
        conn = CdcFluViewConnector(
            data_dir=tmp_path, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)
        )
        snap = run(conn.fetch(REQUEST, fetcher=None))
        assert snap.checksum == "sha256:" + hashlib.sha256(snap.content).hexdigest()
        assert snap.retrieved_at == datetime(2026, 1, 1, tzinfo=UTC)
        assert snap.url == conn.source_url


class TestEndToEnd:
    @pytest.mark.skipif(not REAL_FIXTURE_DIR.is_dir(), reason="real fixture files not present")
    def test_fetch_then_parse_against_real_files(self):
        conn = CdcFluViewConnector(data_dir=REAL_FIXTURE_DIR)
        snapshot = run(conn.fetch(REQUEST, fetcher=None))
        result = conn.parse(snapshot, REQUEST)
        assert result.rows_rejected == 0
        assert len(result.observations) > 0
        indicators = {o.indicator for o in result.observations}
        assert indicators == {
            "ili_pct_weighted",
            "influenza_positivity_clinical",
            "influenza_positivity_public_health",
        }

    def test_parse_rejects_a_bundle_missing_an_entry(self):
        import io
        import zipfile

        from app.ingestion.errors import SchemaMismatchError
        from app.ingestion.models import Snapshot

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(ILINET_ENTRY, "not,real,data\n")
        snapshot = Snapshot(
            url="test://x",
            content=buf.getvalue(),
            checksum="sha256:deadbeef",
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="application/zip",
            source_published_at=None,
        )
        conn = CdcFluViewConnector()
        with pytest.raises(SchemaMismatchError):
            conn.parse(snapshot, REQUEST)

    def test_signature_is_stable_regardless_of_requested_countries(self):
        # query_parameters() is deliberately request-independent: this connector always reads the
        # same fixed local bundle (countries only filter rows at parse time, not what gets
        # fetched), so the change-detection signature must not vary with the request either.
        conn = CdcFluViewConnector()
        sig_a = conn.signature(REQUEST)
        sig_b = conn.signature(IngestRequest(countries=frozenset({"US", "PAK"}), since_year=None))
        assert sig_a == sig_b

    def test_signature_changes_when_parser_version_changes(self):
        conn = CdcFluViewConnector()
        original_version = type(conn).parser_version
        try:
            sig_a = conn.signature(REQUEST)
            type(conn).parser_version = original_version + "-v2"
            sig_b = conn.signature(REQUEST)
        finally:
            type(conn).parser_version = original_version
        assert sig_a != sig_b
