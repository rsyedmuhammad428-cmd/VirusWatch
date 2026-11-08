"""Pure validation tests for app.ai.models."""

from __future__ import annotations

import pytest

from app.ai.models import EvidenceBundle, QuestionRequest


def test_question_request_rejects_blank_text():
    with pytest.raises(ValueError, match="blank"):
        QuestionRequest(text="   ")


def test_question_request_rejects_overlong_text():
    with pytest.raises(ValueError, match="2000"):
        QuestionRequest(text="x" * 2001)


def test_question_request_accepts_text_at_the_limit():
    QuestionRequest(text="x" * 2000)  # must not raise


def test_question_request_user_id_defaults_to_none():
    q = QuestionRequest(text="hello")
    assert q.user_id is None


def test_evidence_bundle_is_empty_when_both_collections_empty():
    assert EvidenceBundle().is_empty


def test_evidence_bundle_not_empty_with_only_structured():
    bundle = EvidenceBundle(structured=("placeholder",))  # type: ignore[arg-type]
    assert not bundle.is_empty
