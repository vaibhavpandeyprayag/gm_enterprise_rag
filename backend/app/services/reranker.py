"""Cross-encoder reranking of the retrieved chunks.

The CrossEncoder is expensive to load, so it is a process-wide singleton and the
application warms it up once at startup. Every failure path (model not
installed, download error, prediction error) degrades to the order the chunks
came back from the vector search.
"""

from __future__ import annotations

import threading
from functools import lru_cache

from langchain_core.documents import Document

from app.config import get_settings

# Cached load failure, so a broken installation is not retried on every request.
_load_error: Exception | None = None
_load_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_reranker():
    """Load (once) and return the CrossEncoder reranker."""
    # Imported lazily: sentence-transformers / torch are heavy and optional at
    # import time (the app must still start when the model is unavailable).
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().reranker_model)


def warm_up() -> None:
    """Best-effort preload at startup - never prevents the app from starting."""
    global _load_error
    try:
        with _load_lock:
            get_reranker()
        print(f"[reranker] loaded {get_settings().reranker_model}")
    except Exception as exc:
        _load_error = exc
        print(f"[reranker] unavailable, keeping vector-search order: {exc}")


def rerank(query: str, documents: list[Document], top_n: int) -> list[Document]:
    """Keep the `top_n` documents most relevant to `query`.

    On any failure the incoming (vector-search) order is used instead.
    """
    global _load_error
    if top_n <= 0 or not documents:
        return []
    if len(documents) == 1 or _load_error is not None:
        return documents[:top_n]

    try:
        with _load_lock:
            model = get_reranker()
    except Exception as exc:
        _load_error = exc
        print(f"[reranker] unavailable, keeping vector-search order: {exc}")
        return documents[:top_n]

    try:
        scores = model.predict([(query, doc.page_content) for doc in documents])
    except Exception as exc:
        print(f"[reranker] prediction failed, keeping vector-search order: {exc}")
        return documents[:top_n]

    order = sorted(
        range(len(documents)),
        key=lambda index: float(scores[index]),
        reverse=True,
    )
    return [documents[index] for index in order[:top_n]]
