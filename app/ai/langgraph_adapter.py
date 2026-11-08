"""Optional LangGraph wrapper around the workflow functions in app.ai.workflow.

promprt2.txt asks for "a multi-step AI research assistant workflow" built with LangGraph, but
also says (via Prompt.txt §12) to "introduce LangGraph only if a genuine need for persistent
multistep orchestration arises" and to build the workflow itself as ordinary functions first.
This module satisfies the first ask without contradicting the second: it is a thin, typed
StateGraph whose nodes call straight into app.ai.workflow's already-tested functions, so there
is exactly one implementation of the decision logic, not two to keep in sync.

Import-guarded: `langgraph` cannot be installed in this sandbox (no PyPI access). Everything
here is written and the state-shape/typing is checked by tests/ai/test_langgraph_adapter.py,
but the actual compiled graph has never been run -- see docs/PHASE5_REPORT.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from app.ai.models import EvidenceBundle, QuestionRequest, WorkflowAnswer
from app.ai.ports import DocumentRetriever, EmbeddingProvider, LLMProvider, StructuredDataSource
from app.ai.workflow import (
    _generate_with_bounded_retries,  # noqa: PLC2701 - same module family
    build_citations,
    build_evidence_bundle,
    build_limitations,
    classify_intent,
    last_update_timestamps,
    reporting_periods,
)


class GraphState(TypedDict, total=False):
    """Typed graph state (promprt2.txt: "typed graph state"). Each node reads/writes a subset."""

    request: QuestionRequest
    intent: str
    bundle: EvidenceBundle
    answer: WorkflowAnswer


class GraphDeps:
    """Bundles the Protocol-backed dependencies every node needs (bound via closures, not global
    state, so a single compiled graph is safe to reuse across concurrent requests).
    """

    def __init__(
        self,
        *,
        structured_source: StructuredDataSource,
        embedder: EmbeddingProvider,
        retriever: DocumentRetriever,
        llm: LLMProvider | None,
    ) -> None:
        self.structured_source = structured_source
        self.embedder = embedder
        self.retriever = retriever
        self.llm = llm


def _import_langgraph():
    try:
        from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "langgraph is not installed in this environment (see requirements.txt and "
            "docs/PHASE5_REPORT.md); use app.ai.workflow.answer_question() directly instead."
        ) from exc
    return StateGraph, END


def build_graph(deps: GraphDeps) -> Any:
    """Builds and compiles the StateGraph. Raises RuntimeError if langgraph is not installed."""
    StateGraph, END = _import_langgraph()

    async def interpret(state: GraphState) -> GraphState:
        return {"intent": classify_intent(state["request"].text)}

    async def retrieve(state: GraphState) -> GraphState:
        bundle = await build_evidence_bundle(
            state["request"],
            state["intent"],  # type: ignore[arg-type]
            structured_source=deps.structured_source,
            embedder=deps.embedder,
            retriever=deps.retriever,
        )
        return {"bundle": bundle}

    async def generate_and_verify(state: GraphState) -> GraphState:
        request = state["request"]
        bundle = state["bundle"]
        intent = state["intent"]  # type: ignore[assignment]
        limitations = build_limitations(bundle, intent)  # type: ignore[arg-type]

        if bundle.is_empty:
            from app.ai.workflow import INSUFFICIENT_EVIDENCE_TEXT

            return {
                "answer": WorkflowAnswer(
                    answer_text=INSUFFICIENT_EVIDENCE_TEXT,
                    citations=(),
                    reporting_periods=(),
                    last_update_timestamps=(),
                    limitations=limitations or ("No evidence was available for this question.",),
                    intent=intent,  # type: ignore[arg-type]
                    insufficient_evidence=True,
                )
            }

        draft = await _generate_with_bounded_retries(request, bundle, deps.llm)
        if not draft.citation_ok:
            from app.ai.workflow import INSUFFICIENT_EVIDENCE_TEXT

            return {
                "answer": WorkflowAnswer(
                    answer_text=INSUFFICIENT_EVIDENCE_TEXT,
                    citations=(),
                    reporting_periods=reporting_periods(bundle),
                    last_update_timestamps=last_update_timestamps(bundle),
                    limitations=limitations
                    + ("The draft answer could not be verified against the retrieved evidence.",),
                    intent=intent,  # type: ignore[arg-type]
                    insufficient_evidence=True,
                    model_identifier=draft.model_identifier,
                )
            }

        return {
            "answer": WorkflowAnswer(
                answer_text=draft.text,
                citations=build_citations(bundle),
                reporting_periods=reporting_periods(bundle),
                last_update_timestamps=last_update_timestamps(bundle),
                limitations=limitations,
                intent=intent,  # type: ignore[arg-type]
                insufficient_evidence=False,
                model_identifier=draft.model_identifier,
            )
        }

    graph = StateGraph(GraphState)
    graph.add_node("interpret", interpret)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate_and_verify", generate_and_verify)
    graph.set_entry_point("interpret")
    graph.add_edge("interpret", "retrieve")
    graph.add_edge("retrieve", "generate_and_verify")
    graph.add_edge("generate_and_verify", END)
    return graph.compile()


async def run_graph(deps: GraphDeps, request: QuestionRequest) -> WorkflowAnswer:
    compiled = build_graph(deps)
    final_state: GraphState = await compiled.ainvoke({"request": request})
    return final_state["answer"]
