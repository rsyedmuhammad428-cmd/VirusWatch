"""Tests for app.ai.langchain_adapter.

langchain-core cannot be installed in this sandbox (no PyPI access), so the LangChain base-class
inheritance path is skipped when the import fails; the plain Python logic (delegation to our own
EmbeddingProvider/DocumentRetriever ports) is always tested directly, with or without the package
present, since VirusWatchEmbeddings and VirusWatchDocumentLoader never require the import to be
constructed or used for their own methods -- only as_langchain_documents() needs it.
"""

from __future__ import annotations

import importlib.util

import pytest

from app.ai.langchain_adapter import VirusWatchDocumentLoader, VirusWatchEmbeddings
from tests.ai.fakes import FakeDocumentRetriever, FakeEmbeddingProvider
from tests.ai.test_workflow import _doc

LANGCHAIN_CORE_INSTALLED = importlib.util.find_spec("langchain_core") is not None


async def test_aembed_query_delegates_to_the_embedding_provider():
    provider = FakeEmbeddingProvider(dimensions=3)
    embeddings = VirusWatchEmbeddings(provider)
    vector = await embeddings.aembed_query("what is dengue?")
    assert vector == [0.0, 1.0, 2.0]
    assert provider.queries == ["what is dengue?"]


async def test_aembed_documents_embeds_each_text_independently():
    provider = FakeEmbeddingProvider(dimensions=2)
    embeddings = VirusWatchEmbeddings(provider)
    vectors = await embeddings.aembed_documents(["first", "second"])
    assert len(vectors) == 2
    assert provider.queries == ["first", "second"]


def test_sync_embed_query_is_not_supported():
    embeddings = VirusWatchEmbeddings(FakeEmbeddingProvider())
    with pytest.raises(NotImplementedError, match="async-only"):
        embeddings.embed_query("x")


def test_sync_embed_documents_is_not_supported():
    embeddings = VirusWatchEmbeddings(FakeEmbeddingProvider())
    with pytest.raises(NotImplementedError, match="async-only"):
        embeddings.embed_documents(["x"])


async def test_document_loader_returns_langchain_document_shaped_dicts():
    retriever = FakeDocumentRetriever(documents=[_doc()])
    loader = VirusWatchDocumentLoader(retriever, FakeEmbeddingProvider())
    results = await loader.aload_for_query(
        "dengue symptoms", country_code="PK", disease="dengue", k=3
    )
    assert len(results) == 1
    item = results[0]
    assert item["page_content"] == _doc().content
    assert item["metadata"]["source_url"] == "https://www.who.int/dengue"
    assert item["metadata"]["authority_level"] == "official"
    assert retriever.last_call == {"country_code": "PK", "disease": "dengue", "match_count": 3}


async def test_document_loader_embeds_the_query_text_first():
    embedder = FakeEmbeddingProvider()
    loader = VirusWatchDocumentLoader(FakeDocumentRetriever(documents=[]), embedder)
    await loader.aload_for_query("what are the symptoms?")
    assert embedder.queries == ["what are the symptoms?"]


def test_as_langchain_documents_raises_clearly_when_package_missing():
    if LANGCHAIN_CORE_INSTALLED:
        pytest.skip(
            "langchain-core is installed in this environment; see the installed-branch test"
        )
    loader = VirusWatchDocumentLoader(FakeDocumentRetriever(), FakeEmbeddingProvider())
    with pytest.raises(RuntimeError, match="langchain-core is not installed"):
        loader.as_langchain_documents([{"page_content": "x", "metadata": {}}])


@pytest.mark.skipif(
    not LANGCHAIN_CORE_INSTALLED, reason="langchain-core not installed in this sandbox"
)
def test_as_langchain_documents_builds_real_document_objects_when_installed():
    loader = VirusWatchDocumentLoader(FakeDocumentRetriever(), FakeEmbeddingProvider())
    docs = loader.as_langchain_documents([{"page_content": "hello", "metadata": {"a": 1}}])
    assert docs[0].page_content == "hello"
    assert docs[0].metadata == {"a": 1}
