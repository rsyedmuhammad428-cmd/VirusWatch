from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.ingestion.connectors.nih_bulletin import NihBulletinConnector
from app.ingestion.errors import FetchError, SchemaMismatchError
from app.ingestion.models import IngestRequest, Snapshot

PK = frozenset({"PK"})
REQUEST = IngestRequest(countries=PK, since_year=None)
REAL_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "nih_bulletins"


def run(coro):
    return asyncio.run(coro)


class TestFetch:
    def test_missing_directory_raises_fetch_error(self, tmp_path):
        conn = NihBulletinConnector(data_dir=tmp_path / "does_not_exist")
        with pytest.raises(FetchError) as exc:
            run(conn.fetch(REQUEST, fetcher=None))
        assert "nih.org.pk" in str(exc.value)

    def test_empty_directory_raises_fetch_error(self, tmp_path):
        conn = NihBulletinConnector(data_dir=tmp_path)
        with pytest.raises(FetchError) as exc:
            run(conn.fetch(REQUEST, fetcher=None))
        assert "No PDF files found" in str(exc.value)

    def test_snapshot_bundles_every_pdf_found_and_checksum_is_deterministic(self, tmp_path):
        (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 fake a")
        (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4 fake b")
        conn = NihBulletinConnector(
            data_dir=tmp_path, clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)
        )
        snap1 = run(conn.fetch(REQUEST, fetcher=None))
        snap2 = run(conn.fetch(REQUEST, fetcher=None))
        assert snap1.checksum == snap2.checksum
        assert snap1.retrieved_at == datetime(2026, 1, 1, tzinfo=UTC)
        assert snap1.url == conn.source_url

    def test_snapshot_changes_when_a_pdf_is_added(self, tmp_path):
        (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 fake a")
        conn = NihBulletinConnector(data_dir=tmp_path)
        snap1 = run(conn.fetch(REQUEST, fetcher=None))
        (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4 fake b")
        snap2 = run(conn.fetch(REQUEST, fetcher=None))
        assert snap1.checksum != snap2.checksum

    def test_ignores_non_pdf_files(self, tmp_path):
        (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 fake a")
        (tmp_path / "readme.txt").write_bytes(b"not a pdf")
        conn = NihBulletinConnector(data_dir=tmp_path)
        snap = run(conn.fetch(REQUEST, fetcher=None))
        assert b"readme" not in snap.content


class TestParseRealBulletin:
    @pytest.mark.skipif(not REAL_FIXTURE_DIR.is_dir(), reason="real bulletin PDF not present")
    def test_fetch_then_parse_against_the_real_week_26_bulletin(self):
        conn = NihBulletinConnector(data_dir=REAL_FIXTURE_DIR)
        snapshot = run(conn.fetch(REQUEST, fetcher=None))
        result = conn.parse(snapshot, REQUEST)
        assert result.rows_rejected == 0
        assert len(result.observations) > 0
        assert all(o.country_code == "PK" for o in result.observations)
        assert all(o.indicator == "suspected_cases_reported" for o in result.observations)
        assert all(o.indicator_type == "other" for o in result.observations)
        diseases = {o.disease for o in result.observations}
        assert "malaria" in diseases
        # Every reporting week in the real bulletin has at least one non-reporting province
        # (AJK and Punjab were both silent in Week 26), so the flag must show up somewhere.
        assert any("total_excludes_non_reporting" in o.quality_flags for o in result.observations)
        # Province-level detail must survive even though it is not independently persisted yet.
        malaria = next(o for o in result.observations if o.disease == "malaria")
        assert "province_counts" in malaria.metadata
        assert isinstance(malaria.metadata["province_counts"], dict)
        assert len(malaria.metadata["province_counts"]) == 7  # all 7 provinces/areas listed


class TestParseErrorHandling:
    def test_parse_raises_on_a_pdf_with_no_table_1(self):
        # A snapshot bundling a byte string that is not a real PDF: extract_pdf will fail, the
        # connector turns that into a warning rather than a hard failure (one bad file should not
        # sink a whole multi-file ingest run), and then raises because nothing usable came out of
        # any file in the bundle.
        conn = NihBulletinConnector()
        name, data = "not_a_real_pdf.pdf", b"this is not a pdf"
        content = len(data).to_bytes(8, "big") + name.encode() + b"\x00" + data
        snapshot = Snapshot(
            url=conn.source_url,
            content=content,
            checksum="sha256:" + hashlib.sha256(content).hexdigest(),
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            content_type="application/octet-stream",
            source_published_at=None,
        )
        with pytest.raises(SchemaMismatchError):
            conn.parse(snapshot, REQUEST)


class TestSignature:
    def test_signature_is_stable_regardless_of_requested_countries(self):
        conn = NihBulletinConnector()
        sig_a = conn.signature(REQUEST)
        sig_b = conn.signature(IngestRequest(countries=frozenset({"PK", "US"}), since_year=None))
        assert sig_a == sig_b

    def test_signature_changes_when_parser_version_changes(self):
        conn = NihBulletinConnector()
        original_version = type(conn).parser_version
        try:
            sig_a = conn.signature(REQUEST)
            type(conn).parser_version = original_version + "-v2"
            sig_b = conn.signature(REQUEST)
        finally:
            type(conn).parser_version = original_version
        assert sig_a != sig_b
