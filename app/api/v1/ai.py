"""POST /api/v1/ai/ask -- the AI workflow (Prompt.txt §12), run as role `anon` (public_conn): the
structured-data read goes through the same RLS the public /surveillance endpoints use, and only
`review_status = 'approved'` documents are ever retrievable (enforced inside
match_document_chunks(), SECURITY INVOKER).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncConnection

from app.ai.models import QuestionRequest
from app.ai.service import AskService
from app.api.deps import get_db, get_settings_dep, public_conn
from app.config import Settings
from app.core.errors import AppError
from app.db import Database
from app.rag.sql_repository import SqlRagRepository

router = APIRouter(prefix="/ai", tags=["ai"])

Conn = Annotated[AsyncConnection, Depends(public_conn)]
Db = Annotated[Database, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    country_code: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    disease: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")


class CitationOut(BaseModel):
    marker: str
    kind: str
    label: str
    source_url: str | None
    retrieved_at: str | None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    reporting_periods: list[str]
    last_update_timestamps: list[str]
    limitations: list[str]
    intent: str
    insufficient_evidence: bool
    workflow_version: str
    model_identifier: str | None


@router.post("/ask", response_model=AskResponse, summary="Evidence-grounded surveillance Q&A")
async def ask(body: AskRequest, conn: Conn, db: Db, settings: SettingsDep) -> AskResponse:
    try:
        request = QuestionRequest(
            text=body.question,
            country_code=body.country_code.upper() if body.country_code else None,
            disease=body.disease,
        )
    except ValueError as exc:
        raise AppError(422, "invalid_question", str(exc)) from exc

    repository = SqlRagRepository(db)
    service = AskService(conn, repository, settings)
    answer = await service.ask(request)

    return AskResponse(
        answer=answer.answer_text,
        citations=[
            CitationOut(
                marker=c.marker,
                kind=c.kind,
                label=c.label,
                source_url=c.source_url,
                retrieved_at=c.retrieved_at.isoformat() if c.retrieved_at else None,
            )
            for c in answer.citations
        ],
        reporting_periods=list(answer.reporting_periods),
        last_update_timestamps=list(answer.last_update_timestamps),
        limitations=list(answer.limitations),
        intent=answer.intent,
        insufficient_evidence=answer.insufficient_evidence,
        workflow_version=answer.workflow_version,
        model_identifier=answer.model_identifier,
    )
