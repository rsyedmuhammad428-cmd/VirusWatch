"""Turn a RawDocument into plain text, stripped of navigation and boilerplate.

Two independent paths:
  - extract_html: a dependency-free HTML->text reducer (stdlib html.parser only, since bs4/
    readability-style packages cannot be installed in this sandbox — see requirements.txt note).
  - extract_pdf: shells out to poppler-utils' `pdftotext -layout`, which IS installed in this
    environment (confirmed: /usr/bin/pdftotext), page by page, so each chunk can carry the page
    number promprt2.txt requires ("Process PDFs into chunks while preserving ... page numbers").

Both are pure in the sense that they only touch the bytes they are given (or a temp file holding
those exact bytes) and return data; neither does any network I/O.
"""

from __future__ import annotations

import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

from app.rag.models import ExtractedDocument, ExtractedPage

# Tags whose entire contents are navigation/boilerplate, never document body text.
_SKIP_CONTAINER_TAGS = frozenset(
    {
        "script",
        "style",
        "nav",
        "header",
        "footer",
        "aside",
        "noscript",
        "form",
        "svg",
        "template",
        "title",  # <head><title> duplicates nav chrome, not body content
    }
)

# Void elements per the HTML5 spec: never receive a handle_endtag call, so they must not push
# onto the skip stack (nothing will ever pop them, which would desync every tag that follows).
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
# Elements that commonly mark cookie banners / share bars / breadcrumbs by class or role, even
# outside the structural tags above. Matched against the tag's class/id/role attribute values.
_SKIP_CLASS_HINTS = (
    "cookie",
    "breadcrumb",
    "share",
    "social",
    "nav-",
    "navbar",
    "menu",
    "sidebar",
    "skip-link",
    "pagination",
    "related-links",
    "subscribe",
)
# Block-level tags: emit a paragraph break after them so extracted text keeps document structure
# (needed for the chunker's paragraph-aware splitting).
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "tr",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "article",
        "section",
        "table",
        "ul",
        "ol",
        "blockquote",
    }
)


class _BoilerplateStrippingParser(HTMLParser):
    """Tracks "am I inside a skipped element" with an explicit stack, one entry per non-void
    start tag, popped on the matching end tag. A plain depth counter that only decremented on
    _SKIP_CONTAINER_TAGS end tags (the previous approach) desynced whenever a boilerplate-class
    element nested inside another skip region closed first -- e.g. a breadcrumb <div> inside a
    <header> -- permanently stranding skip_depth above zero and silently dropping every element
    that followed it in the document. Void elements (br, img, ...) never receive a matching
    handle_endtag, so they must never push onto the stack.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_stack: list[bool] = []
        self._skip_depth = 0
        self._parts: list[str] = []
        self._dropped_chars = 0

    def _looks_like_boilerplate(self, attrs: list[tuple[str, str | None]]) -> bool:
        joined = " ".join((v or "") for k, v in attrs if k in ("class", "id", "role")).lower()
        return any(hint in joined for hint in _SKIP_CLASS_HINTS)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            if tag in _BLOCK_TAGS:
                self._parts.append("\n")
            return
        is_skip = tag in _SKIP_CONTAINER_TAGS or self._looks_like_boilerplate(attrs)
        self._skip_stack.append(is_skip)
        if is_skip:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack:
            was_skip = self._skip_stack.pop()
            if was_skip:
                self._skip_depth -= 1
                return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            self._dropped_chars += len(data)
            return
        self._parts.append(data)

    @property
    def text(self) -> str:
        raw = "".join(self._parts)
        lines = (" ".join(line.split()) for line in raw.splitlines())
        return "\n".join(line for line in lines if line).strip()

    @property
    def dropped_chars(self) -> int:
        return self._dropped_chars


def extract_html(html: str) -> ExtractedDocument:
    """Strip nav/footer/script/style and collapse whitespace; HTML has no page concept."""
    parser = _BoilerplateStrippingParser()
    parser.feed(html)
    parser.close()
    text = parser.text
    pages = (ExtractedPage(page_number=1, text=text),) if text else ()
    return ExtractedDocument(pages=pages, dropped_boilerplate_chars=parser.dropped_chars)


class PdfExtractionError(RuntimeError):
    pass


def extract_pdf(content: bytes, *, timeout_seconds: float = 30.0) -> ExtractedDocument:
    """Run `pdftotext -layout` on each page separately so page numbers survive into chunks."""
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "doc.pdf"
        pdf_path.write_bytes(content)
        page_count = _pdf_page_count(pdf_path, timeout_seconds)
        pages: list[ExtractedPage] = []
        for page_number in range(1, page_count + 1):
            out_path = Path(tmp) / f"page-{page_number}.txt"
            _run(
                [
                    "pdftotext",
                    "-layout",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    str(pdf_path),
                    str(out_path),
                ],
                timeout_seconds,
            )
            raw = (
                out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
            )
            cleaned = "\n".join(line.rstrip() for line in raw.splitlines()).strip("\n")
            if cleaned.strip():
                pages.append(ExtractedPage(page_number=page_number, text=cleaned))
        return ExtractedDocument(pages=tuple(pages))


def _pdf_page_count(pdf_path: Path, timeout_seconds: float) -> int:
    proc = _run(["pdfinfo", str(pdf_path)], timeout_seconds)
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise PdfExtractionError("pdfinfo did not report a page count")


def _run(args: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(  # noqa: S603 - args are fixed-prefix poppler-utils calls, not shell text
            args, capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
    except FileNotFoundError as exc:
        raise PdfExtractionError(f"{args[0]} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise PdfExtractionError(f"{args[0]} timed out after {timeout_seconds}s") from exc
    if proc.returncode != 0:
        raise PdfExtractionError(f"{args[0]} failed: {proc.stderr.strip()}")
    return proc
