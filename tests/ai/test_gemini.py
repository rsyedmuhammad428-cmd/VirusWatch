"""Tests for app.ai.gemini: construction/validation logic only.

`google-genai` cannot be installed in this sandbox (no PyPI access), so GeminiLLMProvider.generate
and GeminiEmbeddingProvider.embed have never made a real call to the Gemini API here -- see the
module's own docstring and docs/PHASE5_REPORT.md. What is tested is everything that does not
require the package: constructor validation (missing key/model raises LLMError immediately,
before any network attempt) and the clear "package not installed" error path.
"""

from __future__ import annotations

import importlib.util

import pytest

from app.ai.gemini import GeminiEmbeddingProvider, GeminiLLMProvider, LLMError

GENAI_INSTALLED = importlib.util.find_spec("google.genai") is not None


def test_llm_provider_requires_an_api_key():
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiLLMProvider(api_key="", model="gemini-2.0-flash")


def test_llm_provider_requires_a_model():
    with pytest.raises(LLMError, match="GEMINI_MODEL"):
        GeminiLLMProvider(api_key="test-key", model="")


def test_embedding_provider_requires_an_api_key():
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiEmbeddingProvider(api_key="", model="gemini-embedding-001", dimensions=768)


def test_embedding_provider_requires_a_model():
    with pytest.raises(LLMError, match="GEMINI_EMBEDDING_MODEL"):
        GeminiEmbeddingProvider(api_key="test-key", model="", dimensions=768)


def test_embedding_provider_exposes_its_model_name():
    provider = GeminiEmbeddingProvider(
        api_key="test-key", model="gemini-embedding-001", dimensions=768
    )
    assert provider.model_name == "gemini-embedding-001"


@pytest.mark.skipif(GENAI_INSTALLED, reason="google-genai is installed in this environment")
async def test_generate_raises_a_clear_error_when_the_sdk_is_not_installed():
    from app.ai.ports import GenerationRequest

    provider = GeminiLLMProvider(api_key="test-key", model="gemini-2.0-flash")
    with pytest.raises(LLMError, match="google-genai is not installed"):
        await provider.generate(
            GenerationRequest(
                system_prompt="system",
                user_prompt="question",
                max_output_tokens=100,
                timeout_seconds=5.0,
            )
        )


@pytest.mark.skipif(GENAI_INSTALLED, reason="google-genai is installed in this environment")
async def test_embed_raises_a_clear_error_when_the_sdk_is_not_installed():
    provider = GeminiEmbeddingProvider(
        api_key="test-key", model="gemini-embedding-001", dimensions=768
    )
    with pytest.raises(LLMError, match="google-genai is not installed"):
        await provider.embed(["hello"])
