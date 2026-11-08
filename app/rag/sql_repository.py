"""PostgreSQL implementation of RagRepository (SQLAlchemy Core + asyncpg)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db import Database
from app.rag import sql
from app.rag.models import DocumentUpsert, IngestOutcome


def _vector_literal(values: tuple[float, ...] | None) -> str | None:
    """pgvector's text input format: "[v1,v2,...]". None stays SQL NULL (no embedding yet)."""
    if values is None:
        return None
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _chunk_metadata(upsert: DocumentUpsert, page_number: int | None) -> str:
    m = upsert.metadata
    return json.dumps(
        {
            "title": m.title,
            "publisher": m.publisher,
            "source_url": m.source_url,
            "document_type": m.document_type,
            "authority_level": m.authority_level,
            "country_code": m.country_code,
            "disease": m.disease,
            "published_at": m.published_at.isoformat() if m.published_at else None,
            "retrieved_at": m.retrieved_at.isoformat(),
            "language": m.language,
            "review_status": m.review_status,
            "page_number": page_number,
        }
    )


class SqlRagRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def find_current_version(self, source_url: str) -> tuple[UUID, str] | None:
        async with self._db.session(read_only=True) as conn:
            row = (
                (await conn.execute(text(sql.FIND_CURRENT_VERSION), {"source_url": source_url}))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return UUID(row["id"]), row["checksum"]

    async def upsert_document(
        self, upsert: DocumentUpsert, *, supersedes: UUID | None
    ) -> IngestOutcome:
        m = upsert.metadata
        async with self._db.session() as conn:
            doc_row = (
                (
                    await conn.execute(
                        text(sql.INSERT_DOCUMENT),
                        {
                            "title": m.title,
                            "publisher": m.publisher,
                            "source_url": m.source_url,
                            "document_type": m.document_type,
                            "authority_level": m.authority_level,
                            "country_code": m.country_code,
                            "disease": m.disease,
                            "published_at": m.published_at.isoformat() if m.published_at else None,
                            "source_updated_at": (
                                m.source_updated_at.isoformat() if m.source_updated_at else None
                            ),
                            "retrieved_at": m.retrieved_at.isoformat(),
                            "checksum": upsert.checksum,
                            "language": m.language,
                            "review_status": m.review_status,
                            "content_storage_path": upsert.content_storage_path,
                            "metadata_json": json.dumps({}),
                        },
                    )
                )
                .mappings()
                .first()
            )
            document_id = UUID(doc_row["id"])

            for embedded in upsert.chunks:
                chunk = embedded.chunk
                await conn.execute(
                    text(sql.INSERT_CHUNK),
                    {
                        "document_id": str(document_id),
                        "chunk_index": chunk.chunk_index,
                        "content": chunk.content,
                        "token_count": len(chunk.content) // 4,  # rough estimate; see chunking.py
                        "embedding": _vector_literal(embedded.embedding),
                        "embedding_model": embedded.embedding_model,
                        "metadata_json": _chunk_metadata(upsert, chunk.page_number),
                    },
                )

            if supersedes is not None:
                await conn.execute(
                    text(sql.MARK_SUPERSEDED),
                    {"document_id": str(supersedes), "superseded_by": str(document_id)},
                )

        return IngestOutcome(
            document_id=document_id,
            status="superseded_previous" if supersedes is not None else "inserted",
            chunks_written=len(upsert.chunks),
            superseded_document_id=supersedes,
        )

    async def set_review_status(self, document_id: UUID, review_status: str) -> None:
        async with self._db.session() as conn:
            await conn.execute(
                text(sql.SET_REVIEW_STATUS),
                {"document_id": str(document_id), "review_status": review_status},
            )

    async def get_document(self, document_id: UUID) -> dict[str, Any] | None:
        async with self._db.session(read_only=True) as conn:
            row = (
                (await conn.execute(text(sql.GET_DOCUMENT), {"document_id": str(document_id)}))
                .mappings()
                .first()
            )
        return dict(row) if row is not None else None

    async def count_chunks(self, document_id: UUID) -> int:
        async with self._db.session(read_only=True) as conn:
            row = (
                (await conn.execute(text(sql.COUNT_CHUNKS), {"document_id": str(document_id)}))
                .mappings()
                .first()
            )
        return int(row["n"]) if row is not None else 0

    async def match_chunks(
        self,
        query_embedding: tuple[float, ...],
        *,
        match_count: int,
        country_code: str | None,
        disease: str | None,
    ) -> list[dict[str, Any]]:
        async with self._db.session(read_only=True) as conn:
            rows = (
                (
                    await conn.execute(
                        text(sql.MATCH_CHUNKS),
                        {
                            "query_embedding": _vector_literal(query_embedding),
                            "match_count": match_count,
                            "filter_country": country_code,
                            "filter_disease": disease,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def record_feedback(
        self,
        *,
        user_id: UUID | None,
        query_hash: str,
        retrieved_chunk_ids: list[UUID],
        rating: int | None,
        citations_supported: bool | None,
        comment: str | None,
        workflow_version: str,
        model_identifier: str | None,
    ) -> UUID:
        async with self._db.session() as conn:
            row = (
                (
                    await conn.execute(
                        text(sql.INSERT_FEEDBACK),
                        {
                            "user_id": str(user_id) if user_id else None,
                            "query_hash": query_hash,
                            "retrieved_chunk_ids": [str(c) for c in retrieved_chunk_ids],
                            "rating": rating,
                            "citations_supported": citations_supported,
                            "comment": comment,
                            "workflow_version": workflow_version,
                            "model_identifier": model_identifier,
                        },
                    )
                )
                .mappings()
                .first()
            )
        return UUID(row["id"])
