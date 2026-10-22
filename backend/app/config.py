"""Application configuration.

All settings are read from the project-root `.env` file so the code base stays
modular and configurable - nothing hard-coded besides safe defaults.

Models:
  - LLM         : Groq   (OpenAI-compatible API)
  - Embeddings  : Jina   (OpenAI-compatible API)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
import os

# Project root: .../backend/app/config.py -> parents[2] == repo root
ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

# Load the root .env once at import time
load_dotenv(ROOT_DIR / ".env")


def _env(key: str, default: str | None = None) -> str:
    """Read an environment variable, raising early when a required one is missing."""
    value = os.getenv(key, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError as exc:  # pragma: no cover - misconfiguration guard
        raise RuntimeError(f"{key} must be an integer") from exc


def _env_list(key: str, default: str) -> list[str]:
    return [origin.strip() for origin in _env(key, default).split(",") if origin.strip()]


def _env_bool(key: str, default: bool) -> bool:
    """Read a boolean env var ("1"/"true"/"yes" are truthy)."""
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Typed view over the runtime configuration."""

    # MongoDB
    mongodb_uri: str
    mongodb_database: str
    mongodb_vector_index: str

    # LLM (Groq)
    groq_api_key: str
    groq_base_url: str
    groq_model: str

    # Embeddings (Jina)
    jina_api_key: str
    jina_base_url: str
    jina_model: str
    embedding_dim: int

    # Retrieval / generation
    retrieval_top_k: int
    retrieval_min_score: float
    max_history_messages: int
    max_context_chars: int

    # Query rephrasing (context-dependent follow-ups are made standalone before
    # the vector search)
    query_rephrasing_enabled: bool
    query_rephrase_history_messages: int

    # URL ingestion (web pages are rendered with Playwright and indexed like documents)
    url_fetch_timeout_seconds: int
    url_render_timeout_ms: int
    url_render_wait_ms: int
    url_max_bytes: int

    # Authentication / sessions
    session_ttl_hours: int
    session_cookie_name: str
    session_cookie_secure: bool
    session_cookie_samesite: str
    session_cookie_domain: str | None
    min_password_length: int
    max_password_bytes: int

    # Banking (encrypted account numbers)
    # Comma-separated: the first key encrypts, every key is tried when decrypting
    # (that is what makes a key rotation safe).
    bank_encryption_keys: tuple[str, ...]
    bank_encryption_salt: str

    # Server
    cors_origins: list[str] = field(default_factory=list)

    # Supported ingestion types (extension -> human label)
    supported_types: tuple[str, ...] = (
        ".pdf",
        ".docx",
        ".txt",
        ".md",
        ".csv",
        ".xlsx",
        ".pptx",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor (reads .env only once per process)."""
    return Settings(
        mongodb_uri=_env("MONGODB_URI"),
        mongodb_database=_env("MONGODB_DATABASE", "GenRAG"),
        mongodb_vector_index=_env("MONGODB_VECTOR_INDEX", "vector_index"),
        groq_api_key=_env("GROQ_API_KEY", ""),
        groq_base_url=_env("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        groq_model=_env("GROQ_MODEL", "openai/gpt-oss-120b"),
        jina_api_key=_env("JINA_API_KEY", ""),
        jina_base_url=_env("JINA_BASE_URL", "https://api.jina.ai/v1"),
        jina_model=_env("JINA_MODEL", "jina-embeddings-v3"),
        embedding_dim=_env_int("JINA_EMBEDDING_DIM", 1024),
        retrieval_top_k=_env_int("RETRIEVAL_TOP_K", 6),
        retrieval_min_score=float(os.getenv("RETRIEVAL_MIN_SCORE", "0.30")),
        max_history_messages=_env_int("MAX_HISTORY_MESSAGES", 10),
        max_context_chars=_env_int("MAX_CONTEXT_CHARS", 12000),
        # --- query rephrasing ---
        query_rephrasing_enabled=_env_bool("QUERY_REPHRASING_ENABLED", True),
        query_rephrase_history_messages=_env_int(
            "QUERY_REPHRASE_HISTORY_MESSAGES", 6
        ),
        url_fetch_timeout_seconds=_env_int("URL_FETCH_TIMEOUT_SECONDS", 15),
        url_render_timeout_ms=_env_int("URL_RENDER_TIMEOUT_MS", 30000),
        url_render_wait_ms=_env_int("URL_RENDER_WAIT_MS", 1500),
        url_max_bytes=_env_int("URL_MAX_BYTES", 5_000_000),
        # --- authentication / sessions ---
        session_ttl_hours=_env_int("SESSION_TTL_HOURS", 24),
        session_cookie_name=_env("SESSION_COOKIE_NAME", "session_token"),
        # Secure cookies require HTTPS - enable it when the app is served over TLS.
        session_cookie_secure=_env_bool("SESSION_COOKIE_SECURE", False),
        session_cookie_samesite=_env("SESSION_COOKIE_SAMESITE", "lax"),
        session_cookie_domain=os.getenv("SESSION_COOKIE_DOMAIN") or None,
        min_password_length=_env_int("MIN_PASSWORD_LENGTH", 8),
        # bcrypt silently truncates beyond 72 bytes, so the limit is enforced upfront.
        max_password_bytes=_env_int("MAX_PASSWORD_BYTES", 72),
        # --- banking ---
        bank_encryption_keys=tuple(
            key.strip()
            for key in _env_list("BANK_ENCRYPTION_KEYS", "")
            if key.strip()
        ),
        bank_encryption_salt=_env(
            "BANK_ENCRYPTION_SALT", "genrag.bank.v1"
        ),
        cors_origins=_env_list(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ),
    )
