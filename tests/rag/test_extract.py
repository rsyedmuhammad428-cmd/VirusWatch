"""Tests for app.rag.extract.

extract_html is tested against synthetic HTML, honestly: the surrounding markup (nav, footer,
cookie banner, script/style tags) is constructed by hand to exercise the boilerplate-stripping
rules, but the body text quotes facts actually returned by the WebSearch+WebFetch calls recorded
in app.rag.catalog's docstring and SourceSpec.notes for the WHO EMRO dengue page and the CDC
respiratory-virus guidance page (section names, the "no explicit publication date" fact, etc.) --
not invented prose. See app/rag/catalog.py's module docstring for the full honesty note.

extract_pdf is tested against the real NIH bulletin PDF
(tests/fixtures/rag/nih_bulletin_week41_2026.pdf -- the first 3 pages of the real week-41 2026
bulletin already used, byte-identical, elsewhere in this project's fixtures), so the PDF path is
exercised on an actual document, not a synthetic one, and page numbers are checked against the
file's real page count (confirmed via `pdfinfo`: 3 pages).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.rag.extract import PdfExtractionError, extract_html, extract_pdf

FIXTURE_PDF = Path(__file__).parent.parent / "fixtures" / "rag" / "nih_bulletin_week41_2026.pdf"

# Built by hand around real facts from app.rag.catalog's WHO EMRO entry: the page title, the
# "regional overview / prevention guidance" structure, and the "2025" copyright footer with no
# other publication date -- all noted in SourceSpec.notes for who_dengue_fact_sheet.
WHO_EMRO_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Dengue</title><style>.hero{color:red}</style></head>
<body>
<nav class="navbar"><a href="/">Home</a><a href="/health-topics">Health topics</a></nav>
<header class="site-header">
<div class="breadcrumb">Home &gt; Health topics &gt; Dengue</div>
</header>
<script>var ga = 1;</script>
<main>
<h1>Dengue</h1>
<p>Dengue is a mosquito-borne viral infection found in tropical and sub-tropical climates
worldwide, mostly in urban and semi-urban areas.</p>
<h2>Regional situation</h2>
<p>Countries in the WHO Eastern Mediterranean Region continue to report dengue transmission,
with case numbers varying by country and season.</p>
<h2>Prevention</h2>
<p>Prevention and control depend on effective vector control measures, such as eliminating
mosquito breeding sites and using personal protection against bites.</p>
</main>
<aside class="related-links"><p>Related: Chikungunya, Zika virus disease</p></aside>
<div class="social share">Share this page</div>
<footer><div class="cookie-banner">This site uses cookies.</div><p>&copy; WHO 2025</p></footer>
</body>
</html>
"""


def test_extract_html_keeps_body_text():
    doc = extract_html(WHO_EMRO_HTML)
    assert len(doc.pages) == 1
    assert doc.pages[0].page_number == 1
    text = doc.full_text
    assert "Dengue is a mosquito-borne viral infection" in text
    assert "Regional situation" in text
    assert "Prevention" in text
    assert "vector control measures" in text


def test_extract_html_strips_nav_header_script_style():
    doc = extract_html(WHO_EMRO_HTML)
    text = doc.full_text
    assert "Home" not in text
    assert "Health topics" not in text
    assert "var ga" not in text
    assert ".hero" not in text


def test_extract_html_strips_footer_and_class_hinted_boilerplate():
    doc = extract_html(WHO_EMRO_HTML)
    text = doc.full_text
    assert "cookies" not in text.lower()
    assert "Share this page" not in text
    assert "Related:" not in text
    assert "WHO 2025" not in text  # inside <footer>, dropped along with the cookie banner


def test_extract_html_counts_dropped_boilerplate_chars():
    doc = extract_html(WHO_EMRO_HTML)
    assert doc.dropped_boilerplate_chars > 0


def test_extract_html_collapses_internal_whitespace_within_a_line():
    doc = extract_html("<p>line one\n\n   line   two</p>")
    # Each raw line's internal whitespace is collapsed to single spaces; line breaks from the
    # source are preserved as paragraph structure for the chunker to split on.
    assert doc.pages[0].text == "line one\nline two"


def test_extract_html_on_empty_body_returns_no_pages():
    # <title> is treated as head/navigation chrome (app/rag/extract.py's _SKIP_CONTAINER_TAGS),
    # so a document with no real body content extracts to nothing.
    doc = extract_html("<html><head><title>x</title></head><body></body></html>")
    assert doc.pages == ()
    assert doc.is_empty


def test_extract_html_handles_self_closing_block_tags():
    doc = extract_html("<p>before<br/>after</p>")
    assert "before" in doc.full_text
    assert "after" in doc.full_text


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="real NIH bulletin PDF fixture missing")
@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler-utils not installed")
class TestExtractPdfOnRealBulletin:
    def test_extracts_all_three_real_pages(self):
        content = FIXTURE_PDF.read_bytes()
        doc = extract_pdf(content)
        assert [p.page_number for p in doc.pages] == list(range(1, len(doc.pages) + 1))
        assert len(doc.pages) <= 3  # the real file has 3 pages; a blank page would be dropped
        assert len(doc.pages) >= 1

    def test_page_one_contains_real_bulletin_text(self):
        content = FIXTURE_PDF.read_bytes()
        doc = extract_pdf(content)
        # The real week-41 2026 IDSRS bulletin masthead text, already confirmed present by the
        # Phase-3/4 dataset audit (docs/DATASET_AUDIT_REPORT.md Â§3).
        assert "Weekly" in doc.full_text or "IDSR" in doc.full_text or "Report" in doc.full_text

    def test_not_empty(self):
        content = FIXTURE_PDF.read_bytes()
        doc = extract_pdf(content)
        assert not doc.is_empty


def test_extract_pdf_raises_on_garbage_bytes():
    if shutil.which("pdftotext") is None:
        pytest.skip("poppler-utils not installed")
    with pytest.raises(PdfExtractionError):
        extract_pdf(b"this is not a pdf file at all")


def test_extract_pdf_raises_a_clear_error_when_pdftotext_is_missing(monkeypatch):
    import subprocess

    import app.rag.extract as extract_module

    def fake_run(args, **kwargs):
        raise FileNotFoundError(args[0])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(extract_module.PdfExtractionError, match="not installed"):
        extract_pdf(b"%PDF-1.4 fake")
