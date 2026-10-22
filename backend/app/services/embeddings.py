"""Embedding model - Jina via its OpenAI-compatible API.

jina-embeddings-v3 returns 1024-dim vectors by default (Matryoshka-truncatable
via `dimensions`). Task-specific embeddings are used per call:
  - documents are embedded with task="retrieval.passage"
  - queries    are embedded with task="retrieval.query"
which measurably improves retrieval quality over generic embeddings.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from app.config import get_settings


class JinaEmbeddings(OpenAIEmbeddings):
    """OpenAI-compatible client that adds Jina's per-call `task` parameter."""

    def embed_documents(self, texts: list[str], chunk_size: int | None = None, **kwargs):
        kwargs.setdefault("extra_body", {})["task"] = "retrieval.passage"
        return super().embed_documents(texts, chunk_size, **kwargs)

    def embed_query(self, text: str, **kwargs):
        kwargs.setdefault("extra_body", {})["task"] = "retrieval.query"
        return super().embed_query(text, **kwargs)


@lru_cache(maxsize=1)
def get_embeddings() -> JinaEmbeddings:
    """Singleton embedding model (jina-embeddings-v3, 1024-dim)."""
    settings = get_settings()
    if not settings.jina_api_key:
        raise RuntimeError("JINA_API_KEY is empty - set it in the project-root .env file")
    return JinaEmbeddings(
        model=settings.jina_model,
        base_url=settings.jina_base_url,
        api_key=settings.jina_api_key,
        dimensions=settings.embedding_dim,
        # Always send raw text (never tiktoken token-IDs) - Jina expects strings.
        check_embedding_ctx_length=False,
        request_timeout=120,
        max_retries=2,
    )
