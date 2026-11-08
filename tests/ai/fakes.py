"""In-memory fakes for app.ai.ports."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.models import DocumentEvidence, QuestionRequest, StructuredObservation, StructuredSignal
from app.ai.ports import GenerationRequest, GenerationResult


@dataclass
class FakeStructuredDataSource:
    observations: list[StructuredObservation] = field(default_factory=list)
    signals: list[StructuredSignal] = field(default_factory=list)
    last_call: dict | None = None
    last_signal_call: dict | None = None

    async def fetch_observations(self, *, country_code, disease, limit):
        self.last_call = {"country_code": country_code, "disease": disease, "limit": limit}
        return list(self.observations)

    async def fetch_top_signals(self, *, country_code, limit):
        self.last_signal_call = {"country_code": country_code, "limit": limit}
        return list(self.signals)[:limit]


@dataclass
class FakeEmbeddingProvider:
    model_name_: str = "fake-embedding-model"
    dimensions: int = 4
    queries: list[str] = field(default_factory=list)

    @property
    def model_name(self) -> str:
        return self.model_name_

    async def embed_query(self, text: str):
        self.queries.append(text)
        return tuple(float(i) for i in range(self.dimensions))


@dataclass
class FakeDocumentRetriever:
    documents: list[DocumentEvidence] = field(default_factory=list)
    last_call: dict | None = None

    async def retrieve(self, query_embedding, *, country_code, disease, match_count):
        self.last_call = {
            "country_code": country_code,
            "disease": disease,
            "match_count": match_count,
        }
        return list(self.documents)[:match_count]


@dataclass
class FakeLLMProvider:
    """Replays a scripted sequence of answers, one per call to generate(). If the script runs
    out, repeats the last answer (useful for retry tests)."""

    answers: list[str] = field(default_factory=lambda: ["[S1] placeholder answer."])
    model_identifier: str = "fake-gemini-model"
    calls: list[GenerationRequest] = field(default_factory=list)
    raise_on_call: int | None = None  # 1-based call index that raises LLMError

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        call_number = len(self.calls)
        if self.raise_on_call == call_number:
            from app.ai.gemini import LLMError

            raise LLMError("simulated provider failure")
        index = min(call_number - 1, len(self.answers) - 1)
        return GenerationResult(text=self.answers[index], model_identifier=self.model_identifier)


def make_question(
    text: str = "How many dengue cases this week in Pakistan?", **overrides
) -> QuestionRequest:
    base = {"text": text}
    base.update(overrides)
    return QuestionRequest(**base)
