"""MongoDB Atlas access layer.

A single client is shared by the whole process. Collections used:
  - users          : the implicit owner account (there is no login)
  - files          : one document per ingested file (scoped by user_id)
  - chunks         : text chunks + embeddings (scoped by user_id, served by the
                     Atlas vector index with a `user_id` pre-filter)
  - conversations  : chat sessions with their full message history (scoped by user_id)
  - bank_accounts  : the user's bank accounts (scoped by user_id, account number
                     stored encrypted)
  - transactions   : the user's bank transactions (scoped by user_id, linked to
                     a bank account via account_id)
  - debit_cards    : one debit card per bank account (blocked, never deleted)
  - chequebook_requests : chequebook orders (idempotent per user + key)
  - bank_sessions  : conversation-scoped bank authorization (15 min TTL)
  - workflow_checkpoints / workflow_writes : LangGraph checkpoints (per thread)
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import get_settings


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    """Lazily create the shared MongoClient (sync driver - FastAPI handlers here are sync)."""
    settings = get_settings()
    return MongoClient(
        settings.mongodb_uri,
        appname="genrag",
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
        tz_aware=True,  # keep UTC offsets on datetimes (consistent JSON for the UI)
    )


def get_db() -> Database:
    return get_client()[get_settings().mongodb_database]


def users_collection() -> Collection:
    return get_db()["users"]


def files_collection() -> Collection:
    return get_db()["files"]


def chunks_collection() -> Collection:
    return get_db()["chunks"]


def collections_collection() -> Collection:  # conversations collection (name avoids shadowing)
    return get_db()["conversations"]


def bank_accounts_collection() -> Collection:
    return get_db()["bank_accounts"]


def transactions_collection() -> Collection:
    return get_db()["transactions"]


def debit_cards_collection() -> Collection:
    return get_db()["debit_cards"]


def chequebook_requests_collection() -> Collection:
    return get_db()["chequebook_requests"]


def bank_sessions_collection() -> Collection:
    return get_db()["bank_sessions"]


def ping() -> bool:
    """Cheap connectivity check used by /api/health and startup logging."""
    try:
        get_client().admin.command("ping")
        return True
    except Exception:
        return False


def _ensure_index(
    collection: Collection,
    keys: list[tuple[str, int]],
    *,
    unique: bool = False,
    name: str | None = None,
) -> None:
    """Create one index, ignoring failures.

    Each index is handled on its own so that one conflict (e.g. an index that
    already exists under a different name) never blocks the remaining ones.
    """
    try:
        collection.create_index(keys, unique=unique, name=name)
    except Exception as exc:  # pragma: no cover - depends on cluster state
        # An equivalent index may already exist under the default name.
        pattern = dict(keys)
        try:
            for existing in collection.list_indexes():
                if (
                    dict(existing.get("key", {})) == pattern
                    and existing.get("unique", False) == unique
                ):
                    return
        except Exception:
            pass
        print(f"[mongo] index {name or keys} skipped: {exc}")


def ensure_indexes() -> None:
    """Create regular (non-vector) indexes; failures are logged, never fatal."""
    _ensure_index(users_collection(), [("username", 1)], unique=True)
    # Files are unique per user (the same name can exist for two users).
    _ensure_index(files_collection(), [("user_id", 1)])
    _ensure_index(
        files_collection(),
        [("filename", 1), ("user_id", 1)],
        unique=True,
        name="filename_user_unique",
    )
    # A leftover global unique index on `filename` (pre multi-tenancy) would stop
    # two users from ingesting a file with the same name - drop it.
    try:
        for existing in files_collection().list_indexes():
            if dict(existing.get("key", {})) == {"filename": 1} and existing.get("unique"):
                files_collection().drop_index(existing["name"])
                print(f"[mongo] dropped stale index '{existing['name']}' on files")
    except Exception as exc:  # pragma: no cover - depends on cluster state
        print(f"[mongo] stale index cleanup skipped: {exc}")
    _ensure_index(collections_collection(), [("user_id", 1)])
    _ensure_index(collections_collection(), [("updated_at", -1)])
    _ensure_index(chunks_collection(), [("file_id", 1)])
    _ensure_index(chunks_collection(), [("user_id", 1)])
    _ensure_index(
        chunks_collection(), [("user_id", 1), ("file_id", 1)], name="user_file_lookup"
    )

    # --- banking ---------------------------------------------------------
    _ensure_index(bank_accounts_collection(), [("user_id", 1)])
    # The same account number must not be stored twice for one user.
    _ensure_index(
        bank_accounts_collection(),
        [("user_id", 1), ("account_number_fingerprint", 1)],
        unique=True,
        name="user_account_number_unique",
    )
    _ensure_index(transactions_collection(), [("user_id", 1)])
    _ensure_index(
        transactions_collection(),
        [("user_id", 1), ("account_id", 1)],
        name="user_account_lookup",
    )
    _ensure_index(
        transactions_collection(),
        [("user_id", 1), ("posted_at", -1)],
        name="user_posted_at",
    )

    # --- bank workflow ---------------------------------------------------
    # One debit card per account (blocking is an update, never a delete).
    _ensure_index(
        debit_cards_collection(),
        [("account_id", 1)],
        unique=True,
        name="account_card_unique",
    )
    _ensure_index(debit_cards_collection(), [("user_id", 1)])
    # A retried request must not create a second chequebook order.
    _ensure_index(
        chequebook_requests_collection(),
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="chequebook_idempotency",
    )
    _ensure_index(chequebook_requests_collection(), [("user_id", 1)])
    # Conversation-scoped authorization sessions (server-side, 15 min).
    _ensure_index(
        bank_sessions_collection(),
        [("conversation_id", 1)],
        unique=True,
        name="conversation_session_unique",
    )
    try:
        bank_sessions_collection().create_index(
            [("expires_at", 1)], expireAfterSeconds=0
        )
    except Exception as exc:  # pragma: no cover - depends on cluster state
        print(f"[mongo] bank session TTL index skipped: {exc}")


def _index_dimensions(index: dict) -> int | None:
    """Extract the vector width from an Atlas search index definition."""
    definition = index.get("latestDefinition") or index.get("definition") or {}
    for field_def in definition.get("fields", []):
        if field_def.get("type") == "vector" and "numDimensions" in field_def:
            return int(field_def["numDimensions"])
    return None


def _index_filter_paths(index: dict) -> set[str]:
    """Extract the filterable paths declared in an Atlas search index."""
    definition = index.get("latestDefinition") or index.get("definition") or {}
    return {
        field_def.get("path")
        for field_def in definition.get("fields", [])
        if field_def.get("type") == "filter"
    }


def ensure_vector_index() -> None:
    """Create the Atlas Vector Search index if missing (or rebuild it when the
    embedding dimension changed, e.g. after switching embedding models, or when
    the `user_id` filter field is missing - required for per-user retrieval).

    Uses the shared index helper from langchain-mongodb (no embedding calls).
    """
    settings = get_settings()
    collection = chunks_collection()
    name = settings.mongodb_vector_index

    try:
        from langchain_mongodb.index import create_vector_search_index

        # Inspect the existing index: it must match the embedding width and
        # expose `user_id` as a filter field, otherwise $vectorSearch cannot
        # restrict results to a single user.
        try:
            existing = next(iter(collection.list_search_indexes(name)), None)
        except Exception:
            existing = None

        if existing is not None:
            current_dim = _index_dimensions(existing)
            filter_paths = _index_filter_paths(existing)
            dim_ok = current_dim is None or current_dim == settings.embedding_dim
            filter_ok = "user_id" in filter_paths
            if not (dim_ok and filter_ok):
                reason = []
                if not dim_ok:
                    reason.append(f"dim {current_dim} != {settings.embedding_dim}")
                if not filter_ok:
                    reason.append("missing 'user_id' filter field")
                print(f"[mongo] rebuilding vector index ({', '.join(reason)})")
                try:
                    collection.drop_search_index(name)
                except Exception as exc:
                    print(f"[mongo] drop failed (continuing): {exc}")

        create_vector_search_index(
            collection=collection,
            index_name=name,
            path="embedding",
            dimensions=settings.embedding_dim,
            similarity="cosine",
            filters=["user_id"],  # enables per-user retrieval filters
        )
        print(
            f"[mongo] vector index '{name}' is ready "
            f"(dim={settings.embedding_dim}, model={settings.jina_model}, "
            f"filter=user_id)"
        )
    except Exception as exc:
        # Common when the index already exists or Atlas is still provisioning it.
        print(f"[mongo] vector index check skipped: {exc}")


def doc_to_dict(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a Mongo document (ObjectId values) into a JSON-safe dict."""
    if doc is None:
        return None
    from bson import ObjectId

    def _convert(value: Any) -> Any:
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_convert(v) for v in value]
        return value

    return {k: _convert(v) for k, v in doc.items()}
