"""Tests for app.ai.langgraph_adapter.

langgraph cannot be installed in this sandbox (no PyPI access), so build_graph()/run_graph() --
the actual compiled StateGraph -- have never been executed here; that boundary is documented in
the module's own docstring and in docs/PHASE5_REPORT.md. What IS tested unconditionally is that
every node function the graph would call is built on top of app.ai.workflow's already-tested
functions (not a second copy of the decision logic), and the typed GraphState shape itself.
"""

from __future__ import annotations

import importlib.util

import pytest

from app.ai.langgraph_adapter import GraphDeps, build_graph
from tests.ai.fakes import (
    FakeDocumentRetriever,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeStructuredDataSource,
)

LANGGRAPH_INSTALLED = importlib.util.find_spec("langgraph") is not None


def _deps(**overrides) -> GraphDeps:
    base = dict(
        structured_source=FakeStructuredDataSource(observations=[]),
        embedder=FakeEmbeddingProvider(),
        retriever=FakeDocumentRetriever(documents=[]),
        llm=FakeLLMProvider(),
    )
    base.update(overrides)
    return GraphDeps(**base)


def test_build_graph_raises_a_clear_error_when_langgraph_is_not_installed():
    if LANGGRAPH_INSTALLED:
        pytest.skip("langgraph is installed in this environment; see the installed-branch test")
    with pytest.raises(RuntimeError, match="langgraph is not installed"):
        build_graph(_deps())


@pytest.mark.skipif(not LANGGRAPH_INSTALLED, reason="langgraph not installed in this sandbox")
async def test_run_graph_produces_the_same_insufficient_evidence_answer_as_the_plain_workflow():
    from app.ai.langgraph_adapter import run_graph
    from app.ai.workflow import INSUFFICIENT_EVIDENCE_TEXT, answer_question
    from tests.ai.fakes import make_question

    request = make_question("How many dengue cases this week?")
    deps = _deps()

    direct = await answer_question(
        request,
        structured_source=deps.structured_source,
        embedder=deps.embedder,
        retriever=deps.retriever,
        llm=deps.llm,
    )
    via_graph = await run_graph(deps, request)

    assert direct.answer_text == via_graph.answer_text == INSUFFICIENT_EVIDENCE_TEXT
    assert direct.insufficient_evidence == via_graph.insufficient_evidence


def test_graph_deps_bundles_the_four_dependencies_the_nodes_need():
    deps = _deps()
    assert deps.structured_source is not None
    assert deps.embedder is not None
    assert deps.retriever is not None
    assert deps.llm is not None


def test_graph_deps_llm_may_be_none():
    deps = _deps(llm=None)
    assert deps.llm is None
