"""MongoDB Atlas vector store (LangChain) for the `chunks` collection.

Every chunk belongs to a user: `user_id` is stored as a top-level field (that is
how langchain-mongodb flattens metadata) and is indexed as a *filter* field in
the Atlas Vector Search index. Retrieval therefore only ever sees the chunks of
the user who is asking.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_core.documents import Document
from langchain_mongodb import MongoDBAtlasVectorSearch

from app.config import get_settings
from app.services import mongo
from app.services.embeddings import get_embeddings


@lru_cache(maxsize=1)
def get_vector_store() -> MongoDBAtlasVectorSearch:
    """Singleton vector store.

    The Atlas vector index itself is created at startup by
    `mongo.ensure_vector_index()` (no embedding call needed for that).
    """
    settings = get_settings()
    return MongoDBAtlasVectorSearch(
        collection=mongo.chunks_collection(),
        embedding=get_embeddings(),
        index_name=settings.mongodb_vector_index,
        text_key="text",
        embedding_key="embedding",
        dimensions=settings.embedding_dim,
        relevance_score_fn="cosine",
        auto_create_index=False,
    )


def add_documents(documents: list[Document], file_id: str, user_id: str) -> list[str]:
    """Embed and persist chunks belonging to one file of one user."""
    for doc in documents:
        # file_id/filename/user_id travel inside metadata so deletion and
        # per-user retrieval stay one query each.
        doc.metadata["file_id"] = file_id
        doc.metadata["user_id"] = user_id
    return get_vector_store().add_documents(documents, batch_size=32)


def similarity_search(
    query: str, k: int, user_id: str
) -> list[tuple[Document, float]]:
    """Top-k chunks for a query, restricted to one user.

    Returns (document, relevance score in [0, 1]).
    """
    return get_vector_store().similarity_search_with_score(
        query,
        k=k,
        pre_filter={"user_id": {"$eq": user_id}},
    )


def delete_by_file(file_id: str, user_id: str) -> int:
    """Remove every chunk of a file owned by the user; returns the deleted count.

    langchain-mongodb stores metadata fields at the top level of each document,
    so `file_id` and `user_id` live at the root (not under `metadata`).
    """
    result = mongo.chunks_collection().delete_many(
        {"file_id": file_id, "user_id": user_id}
    )
    return result.deleted_count
