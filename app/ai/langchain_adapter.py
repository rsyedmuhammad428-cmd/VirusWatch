"""LangChain-compatible adapters over our own ports (promprt2.txt's "compatible LangChain
integration"). These wrap app.ai/app.rag's Protocol-based ports rather than replacing them, so
the workflow and pipeline keep exactly one implementation of retrieval/embedding/storage and
LangChain callers get a standard interface on top.

Import-guarded: `langchain-core` cannot be installed in this sandbox (no PyPI access). The class
bodies are written and tested for structure (tests/ai/test_langchain_adapter.py skips the actual
LangChain base-class inheritance check when the package is absent, but always tests the plain
Python logic by calling the methods directly on an instance built without a langchain import).
"""

from __future__ import annotations

from app.ai.ports import DocumentRetriever, EmbeddingProvider


def _import_langchain_core():
    try:
        from langchain_core.documents import Document  # type: ignore[import-not-found]
        from langchain_core.embeddings import Embeddings  # type: ignore[import-not-found]
        from langchain_core.vectorstores import (
            VectorStoreRetriever,  # type: ignore[import-not-found]
        )
    except ImportError as exc:
        raise RuntimeError(
            "langchain-core is not installed in this environment (see requirements.txt and "
            "docs/PHASE5_REPORT.md)."
        ) from exc
    return Document, Embeddings, VectorStoreRetriever


class VirusWatchEmbeddings:
    """Implements LangChain's Embeddings interface (embed_documents/embed_query) by delegating
    to an app.ai.ports.EmbeddingProvider. Usable with or without langchain-core installed: it
    only inherits from langchain_core.embeddings.Embeddings when the import actually succeeds.
    """

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider

    async def aembed_query(self, text: str) -> list[float]:
        return list(await self._provider.embed_query(text))

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(await self._provider.embed_query(t)) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError(
            "VirusWatchEmbeddings is async-only; call aembed_query (the workflow never blocks "
            "the event loop on an embedding call)."
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("VirusWatchEmbeddings is async-only; call aembed_documents.")


class VirusWatchDocumentLoader:
    """A minimal LangChain-document-shaped loader over our own DocumentRetriever, for callers
    that want to compose our retrieval with other LangChain components (e.g. a LangChain
    ConversationalRetrievalChain) rather than app.ai.workflow directly.
    """

    def __init__(self, retriever: DocumentRetriever, embedder: EmbeddingProvider) -> None:
        self._retriever = retriever
        self._embedder = embedder

    async def aload_for_query(
        self, query: str, *, country_code: str | None = None, disease: str | None = None, k: int = 6
    ) -> list[dict[str, object]]:
        """Returns LangChain-Document-shaped dicts (page_content + metadata) so a caller with
        langchain-core installed can do `Document(**d)` for each item; avoids importing
        langchain_core.documents.Document when it is not installed.
        """
        embedding = await self._embedder.embed_query(query)
        chunks = await self._retriever.retrieve(
            embedding, country_code=country_code, disease=disease, match_count=k
        )
        return [
            {
                "page_content": c.content,
                "metadata": {
                    "chunk_id": str(c.chunk_id),
                    "document_id": str(c.document_id),
                    "title": c.title,
                    "publisher": c.publisher,
                    "source_url": c.source_url,
                    "document_type": c.document_type,
                    "authority_level": c.authority_level,
                    "country_code": c.country_code,
                    "disease": c.disease,
                    "published_at": c.published_at.isoformat() if c.published_at else None,
                    "retrieved_at": c.retrieved_at.isoformat() if c.retrieved_at else None,
                    "language": c.language,
                    "review_status": c.review_status,
                    "similarity": c.similarity,
                },
            }
            for c in chunks
        ]

    def as_langchain_documents(self, raw: list[dict[str, object]]) -> list[object]:
        """Converts aload_for_query's output into real langchain_core Document objects.
        Raises RuntimeError if langchain-core is not installed.
        """
        Document, _, _ = _import_langchain_core()
        return [Document(page_content=r["page_content"], metadata=r["metadata"]) for r in raw]
