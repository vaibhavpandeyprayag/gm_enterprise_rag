"""Query decomposition: split a question into standalone subqueries.

A multi-part question ("compare the 2023 revenue and headcount") embeds poorly
as a single vector, so the LLM splits it into one standalone query per
information need. A single request stays a single subquery.

Every failure path returns the original question as the only subquery, so the
pipeline degrades to the previous single-query behaviour.
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import Settings, get_settings
from app.prompts import QUERY_DECOMPOSE_SYSTEM_PROMPT, QUERY_DECOMPOSE_USER_PROMPT
from app.services.history import history_text
from app.services.llm import content_to_text, get_llm

# Leading list markers ("1.", "-", "*", "•") in a plain-text fallback answer.
_LIST_MARKER = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")

# Wrapping quotes the model may put around a subquery.
_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("\u201c", "\u201d"), ("\u2018", "\u2019"))


def _clean_item(item: object) -> str:
    """Normalise one parsed subquery (strip quotes, collapse whitespace)."""
    text = " ".join(str(item).split()).strip()
    for opening, closing in _QUOTE_PAIRS:
        if len(text) >= 2 and text.startswith(opening) and text.endswith(closing):
            text = text[1:-1].strip()
            break
    return text


def _parse_subqueries(raw: str) -> list[str]:
    """Parse the model reply: a JSON array, or one subquery per line.

    Returns the subqueries in order, without duplicates or empty entries.
    """
    text = (raw or "").strip()
    if not text:
        return []

    # Drop markdown code fences, then prefer the JSON array portion.
    text = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("```")
    ).strip()
    start, end = text.find("["), text.rfind("]")
    candidate = text[start : end + 1] if start != -1 and end > start else text

    items: list[object] | None = None
    for source in (candidate, text):
        try:
            data = json.loads(source)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            items = data
            break
        if isinstance(data, str):
            items = [data]
            break

    if items is None:
        # Not JSON - treat each non-empty line as one subquery.
        items = [
            _LIST_MARKER.sub("", line)
            for line in text.splitlines()
            if line.strip()
        ]

    parsed: list[str] = []
    seen: set[str] = set()
    for item in items:
        query = _clean_item(item)
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        parsed.append(query)
    return parsed


def _decompose(question: str, history: list[dict], settings: Settings) -> list[str]:
    """Ask the LLM for the standalone subqueries of `question`."""
    user_prompt = QUERY_DECOMPOSE_USER_PROMPT.format(
        history=history_text(history, settings.decomposition_history_messages),
        question=question,
    )
    messages = [
        SystemMessage(
            content=QUERY_DECOMPOSE_SYSTEM_PROMPT.format(
                max_subqueries=settings.max_subqueries
            )
        ),
        HumanMessage(content=user_prompt),
    ]
    response = get_llm().invoke(messages)
    return _parse_subqueries(content_to_text(response.content))


def decompose_query(
    question: str,
    history: list[dict],
    settings: Settings | None = None,
) -> list[str]:
    """Return the standalone subqueries for `question` (never empty)."""
    original = (question or "").strip()
    if not original:
        return []

    settings = settings or get_settings()
    if not settings.query_decomposition_enabled:
        return [original]

    try:
        subqueries = _decompose(original, history, settings)
    except Exception as exc:  # network/LLM/provider failure -> single query
        print(f"[decompose] failed, using the original question: {exc}")
        return [original]

    subqueries = subqueries[: settings.max_subqueries]
    if not subqueries:
        return [original]
    if len(subqueries) > 1:
        print(f"[decompose] {original!r} -> {subqueries}")
    return subqueries
