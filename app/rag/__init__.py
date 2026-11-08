"""Phase 5: RAG document ingestion (discovery -> extraction -> chunking -> embedding -> storage).

Mirrors the app.ingestion package's shape: pure models and pure transforms (extract.py,
chunking.py) tested directly; a Protocol-based pipeline (pipeline.py) tested with fakes; SQL kept
as string constants (sql.py) executed by a thin repository (sql_repository.py).
"""

from __future__ import annotations
