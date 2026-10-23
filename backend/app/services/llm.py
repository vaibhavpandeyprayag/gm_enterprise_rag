"""Chat model (LLM) - Groq via its OpenAI-compatible API (openai/gpt-oss-120b)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI

from app.config import get_settings


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """Singleton chat model (used for the answer turn and the rephrasing turn)."""
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is empty - set it in the project-root .env file")
    return ChatOpenAI(
        model=settings.groq_model,
        base_url=settings.groq_base_url,
        api_key=settings.groq_api_key,
        temperature=0, 
        # temperature=0.2, # low temp -> grounded, factual answers
        timeout=120,
        max_retries=2,
    )


def content_to_text(content: Any) -> str:
    """Normalise chat-model output (str or content-block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p)
    return str(content)
