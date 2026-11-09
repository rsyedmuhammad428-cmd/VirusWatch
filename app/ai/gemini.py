"""Provider abstraction around the Gemini API (Prompt.txt §11).

Import-guarded: the `google-genai` package cannot be installed in this sandbox (no PyPI access
-- see requirements.txt and docs/PHASE5_REPORT.md), so importing it is deferred to first use.
Everything that does not require the package (timeout handling, error wrapping, the public
interface) is written and unit tested now; the actual API call has never been executed against
a live Gemini endpoint.

Model identifiers are read from settings (GEMINI_MODEL / GEMINI_EMBEDDING_MODEL) rather than
hardcoded, per the spec: "Do not hardcode an assumed model identifier if availability has not
been verified."
"""

from __future__ import annotations

from app.ai.ports import GenerationRequest, GenerationResult


class LLMError(RuntimeError):
    """Raised for any provider failure: missing SDK, missing/invalid API key, timeout, or an
    error response from Gemini. The workflow (app.ai.workflow) must handle this gracefully
    (spec rule 12: "graceful provider-failure handling") rather than let it propagate as a 500.
    """


def _import_genai():
    try:
        from google import genai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise LLMError(
            "google-genai is not installed in this environment; the Gemini provider cannot run "
            "here (see requirements.txt and docs/PHASE5_REPORT.md)."
        ) from exc
    return genai


class GeminiLLMProvider:
    """LLMProvider backed by the Gemini API. Constructed with an api_key and model identifier
    already validated by app.config.Settings; never reads environment variables itself.
    """

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not configured")
        if not model:
            raise LLMError("GEMINI_MODEL is not configured")
        self._api_key = api_key
        self._model = model

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        genai = _import_genai()
        client = genai.Client(api_key=self._api_key)
        try:
            import asyncio

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=self._model,
                    contents=request.user_prompt,
                    config={
                        "system_instruction": request.system_prompt,
                        "max_output_tokens": request.max_output_tokens,
                    },
                ),
                timeout=request.timeout_seconds,
            )
        except TimeoutError as exc:
            raise LLMError(f"Gemini request timed out after {request.timeout_seconds}s") from exc
        except Exception as exc:  # noqa: BLE001 - any SDK/network failure becomes LLMError
            raise LLMError(f"Gemini request failed: {exc}") from exc
        text = getattr(response, "text", None)
        if not text:
            raise LLMError("Gemini returned an empty response")
        return GenerationResult(text=text, model_identifier=self._model)


class GeminiEmbeddingProvider:
    """EmbeddingProvider backed by the Gemini embeddings API."""

    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not configured")
        if not model:
            raise LLMError("GEMINI_EMBEDDING_MODEL is not configured")
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return self._model

    async def embed_query(self, text: str) -> tuple[float, ...]:
        vectors = await self.embed([text])
        return vectors[0]

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        genai = _import_genai()
        client = genai.Client(api_key=self._api_key)
        try:
            import asyncio

            response = await asyncio.to_thread(
                client.models.embed_content,
                model=self._model,
                contents=texts,
                config={"output_dimensionality": self._dimensions},
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini embedding request failed: {exc}") from exc
        embeddings = getattr(response, "embeddings", None)
        if not embeddings or len(embeddings) != len(texts):
            raise LLMError("Gemini embedding response did not match the requested batch size")
        vectors = [tuple(float(value) for value in e.values) for e in embeddings]
        for v in vectors:
            if len(v) != self._dimensions:
                raise LLMError(
                    f"Gemini returned {len(v)}-dimensional embeddings, expected "
                    f"{self._dimensions} (app.config.Settings.embedding_dimensions)"
                )
        # gemini-embedding-001 requires client-side normalization when requesting
        # fewer than its native 3072 dimensions.
        if self._model.removeprefix("models/") == "gemini-embedding-001":
            normalized = []
            for vector in vectors:
                norm = sum(value * value for value in vector) ** 0.5
                if not norm:
                    raise LLMError("Gemini returned a zero-length embedding vector")
                normalized.append(tuple(value / norm for value in vector))
            vectors = normalized
        return vectors

