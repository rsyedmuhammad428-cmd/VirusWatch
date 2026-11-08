"""Split ExtractedDocument text into semantically-coherent TextChunks (Prompt.txt §10 step 5).

Paragraph-aware: never splits mid-paragraph unless a single paragraph alone exceeds
target_chars, in which case it falls back to sentence boundaries, then hard character slicing
as a last resort. Each chunk carries its source page_number (None for HTML, which has no
pages) so PDF-sourced chunks satisfy promprt2.txt's "preserving ... page numbers".
"""

from __future__ import annotations

import re

from app.rag.models import ChunkingConfig, ExtractedDocument, SourceKind, TextChunk

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def _split_paragraph(paragraph: str, target_chars: int) -> list[str]:
    if len(paragraph) <= target_chars:
        return [paragraph]
    sentences = _SENTENCE_BOUNDARY.split(paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > target_chars and current:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    # A single sentence longer than target_chars: hard-slice it so no piece is unbounded.
    final: list[str] = []
    for piece in pieces:
        if len(piece) <= target_chars:
            final.append(piece)
        else:
            final.extend(piece[i : i + target_chars] for i in range(0, len(piece), target_chars))
    return final


def chunk_page_text(text: str, config: ChunkingConfig) -> list[str]:
    """Pure text -> list[str], one page at a time. Public so tests can check boundaries directly."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    units: list[str] = []
    for para in paragraphs:
        units.extend(_split_paragraph(para, config.target_chars))

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) > config.target_chars and current:
            chunks.append(current)
            # carry a tail of the previous chunk forward as overlap, for retrieval continuity
            tail = current[-config.overlap_chars :] if config.overlap_chars else ""
            current = f"{tail}\n\n{unit}".strip() if tail else unit
        else:
            current = candidate
    if current:
        chunks.append(current)

    # Merge a too-short trailing chunk into its predecessor rather than indexing a fragment.
    if len(chunks) >= 2 and len(chunks[-1]) < config.min_chunk_chars:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}"
        chunks.pop()
    return chunks


def chunk_document(
    doc: ExtractedDocument, source_kind: SourceKind, config: ChunkingConfig | None = None
) -> list[TextChunk]:
    """Chunk every page, keeping each chunk's page_number for PDFs; chunk_index is global."""
    config = config or ChunkingConfig()
    result: list[TextChunk] = []
    index = 0
    for page in doc.pages:
        for piece in chunk_page_text(page.text, config):
            result.append(
                TextChunk(
                    chunk_index=index,
                    content=piece,
                    page_number=page.page_number if source_kind is SourceKind.PDF else None,
                )
            )
            index += 1
    return result
