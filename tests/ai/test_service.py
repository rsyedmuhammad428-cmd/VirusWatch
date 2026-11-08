"""Tests for app.ai.service.AskService's provider-construction / graceful-fallback logic.

Only the no-Gemini-configured path is exercised without a real database connection: with
GEMINI_API_KEY unset (the default in tests/conftest.py's make_settings), AskService._build_providers
returns (None, None), which app.ai.workflow.answer_question handles by returning the safe
insufficient-evidence answer -- for a guidance-only question this never touches the structured
data source or the RAG repository, so `conn`/`repository` can be unused sentinels here. The full
wiring against a real Postgres connection is out of scope for this sandbox (see
docs/PHASE5_REPORT.md); tests/ai/test_workflow.py covers the decision logic itself against fakes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")  # app.ai.service imports AsyncConnection; see module docstring

from app.ai.models import QuestionRequest  # noqa: E402
from app.ai.service import AskService  # noqa: E402
from app.ai.workflow import INSUFFICIENT_EVIDENCE_TEXT  # noqa: E402
from tests.conftest import make_settings  # noqa: E402


async def test_ask_falls_back_to_insufficient_evidence_when_gemini_is_not_configured():
    settings = make_settings()  # gemini_api_key/model default to "" (app/config.py)
    assert settings.gemini_api_key.get_secret_value() == ""

    service = AskService(conn=None, repository=None, settings=settings)  # type: ignore[arg-type]
    answer = await service.ask(QuestionRequest(text="What are the symptoms of dengue?"))

    assert answer.insufficient_evidence
    assert answer.answer_text == INSUFFICIENT_EVIDENCE_TEXT
    assert answer.model_identifier is None


async def test_build_providers_returns_none_none_when_unconfigured():
    settings = make_settings()
    service = AskService(conn=None, repository=None, settings=settings)  # type: ignore[arg-type]
    embedder, llm = service._build_providers()
    assert embedder is None
    assert llm is None
