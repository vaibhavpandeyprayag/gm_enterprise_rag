"""Query rephrasing: make a context-dependent question standalone.

A follow-up such as "what about its pricing?" carries almost no signal for the
embedding model, so the vector search misses the relevant chunks. This module
asks the LLM (the same client used for answering) to rewrite such a question
into a self-contained query, and hands that query to the retriever.

Contract:
  - a clear, self-contained question is passed through unchanged
  - the rewrite only happens when the question could depend on the chat history
  - only the final query is returned - no explanation, no quotes, no label
  - any failure (LLM error, unusable output) falls back to the original query
"""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import Settings, get_settings
from app.prompts import QUERY_REPHRASE_SYSTEM_PROMPT, QUERY_REPHRASE_USER_PROMPT
from app.services.history import history_text
from app.services.llm import content_to_text, get_llm

# Longest rewritten query that is accepted (guards against a chatty answer).
MAX_REWRITTEN_QUERY_CHARS = 400

# Greetings and acknowledgements that are never worth a rewrite.
_NON_QUESTIONS = frozenset(
    {
        "yes",
        "no",
        "yeah",
        "yep",
        "nope",
        "sure",
        "true",
        "false",
        "correct",
        "indeed",
        "exactly",
        "right",
        "thank you",
        "thanks a lot",
        "good morning",
        "good evening",
        "good night",
        "how are you",
        "see you",
    }
)

# Openers of a small talk message ("hello there", "thanks a lot").
_GREETING_STARTERS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "yo",
        "thanks",
        "thank",
        "ok",
        "okay",
        "cool",
        "nice",
        "great",
        "awesome",
        "perfect",
        "sure",
        "bye",
        "goodbye",
        "good",
    }
)

# Markers of a question that cannot be understood without the chat history.
# They decide *whether* to ask the LLM - the LLM itself decides *whether* to
# rewrite, so a false positive here only costs one extra (cheap) call.
_CONTEXT_DEPENDENT_PATTERNS: tuple[str, ...] = (
    # Deictic pronouns / determiners: "it", "its", "that", "these", "they"...
    r"\b(?:it|its|this|that|these|those|they|them|their|theirs|he|she|his|her|hers)\b",
    # Back-references to earlier items: "the second one", "the former", "the same"
    r"\b(?:one|ones|first|second|third|fourth|fifth|last|former|latter|previous|prior|above|earlier|mentioned|said|stated|told)\b",
    # Additive / comparative continuations: "also", "as well", "again", "another"
    r"\b(?:also|too|as well|again|another|else|instead|additionally|similarly|same|different)\b",
    # Explicit discourse continuations: "what about", "and then", "you mentioned"
    r"\bwhat\s+about\b|\bhow\s+about\b|\band\s+then\b|\bany\s+way\b"
    r"|\bas\s+(?:mentioned|discussed|stated)\b|\byou\s+(?:said|mentioned)\b"
    r"|\bthe\s+(?:above|previous|former|latter)\b",
)

# "Rewritten query: ..." style labels the model sometimes prepends.
_LABEL_PREFIX = re.compile(
    r"^\s*(?:rewritten|rephrased|standalone|final|search)\s*"
    r"(?:query|question)\s*[:\-]\s*",
    re.IGNORECASE,
)

# The model declining to rewrite ("the query is already clear").
_NO_REWRITE_NEEDED = re.compile(
    r"\b(?:already\s+(?:clear|self[\s\-]?contained|standalone|complete)"
    r"|no\s+(?:rephrasing|rewriting|change)\s+(?:is\s+|are\s+)?(?:needed|required|necessary)"
    r"|original\s+query\s+(?:is\s+)?(?:unchanged|sufficient|clear))\b",
    re.IGNORECASE,
)

# Useless answers instead of a query.
_EMPTY_RESULTS = frozenset({"n/a", "na", "none", "null", "unknown", "-", "no query"})

# Wrapping quotes the model may add around the query.
_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("\u201c", "\u201d"), ("\u2018", "\u2019"))

def _needs_rephrasing(question: str) -> bool:
    """Heuristic: could this question depend on the previous turns?

    Used only to skip the LLM call for questions that are obviously standalone;
    it never blocks a rewrite that the LLM would consider useful.
    """
    text = question.strip().lower()
    if not text:
        return False
    # Greetings / acknowledgements ("thanks!", "ok") are never worth a rewrite.
    normalized = text.strip(" .!?,;:'\"()[]")
    if not normalized or normalized in _NON_QUESTIONS:
        return False
    # A very short question is almost always elliptical ("and pricing?").
    words = normalized.split()
    if len(words) <= 3:
        # ... unless it is small talk ("hello there").
        return words[0] not in _GREETING_STARTERS
    return any(re.search(pattern, text) for pattern in _CONTEXT_DEPENDENT_PATTERNS)

def _clean_output(raw: str) -> str:
    """Extract the final query from the model's answer.

    Removes code fences, labels, wrapping quotes and markdown so that only the
    query itself is left.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("```")
    ]
    if not lines:
        return ""
    query = lines[0]
    query = _LABEL_PREFIX.sub("", query).strip()
    for opening, closing in _QUOTE_PAIRS:
        if len(query) >= 2 and query.startswith(opening) and query.endswith(closing):
            query = query[1:-1].strip()
            break
    query = query.replace("**", "").replace("*", "").strip()
    # Keep it a single line.
    return " ".join(query.split())


def _rewrite(question: str, history: list[dict], settings: Settings) -> str:
    """Ask the LLM for a standalone version of `question`."""
    user_prompt = QUERY_REPHRASE_USER_PROMPT.format(
        history=history_text(history, settings.query_rephrase_history_messages),
        question=question,
    )
    messages = [
        SystemMessage(content=QUERY_REPHRASE_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    response = get_llm().invoke(messages)
    return _clean_output(content_to_text(response.content))


def rephrase_query(
    question: str,
    history: list[dict],
    settings: Settings | None = None,
) -> str:
    """Return a standalone version of `question` for the retriever.

    The original question is returned unchanged when no rewrite is needed or
    when the rewrite fails.
    """
    original = (question or "").strip()
    if not original:
        return original

    settings = settings or get_settings()
    if not settings.query_rephrasing_enabled:
        return original
    # Without history there is nothing to resolve a reference against.
    if not history:
        return original
    if not _needs_rephrasing(original):
        return original

    try:
        rewritten = _rewrite(original, history, settings)
    except Exception as exc:  # network/LLM/provider failure -> keep the original
        print(f"[rephrase] rewrite failed, using the original query: {exc}")
        return original

    # Reject unusable output and fall back to the original question.
    if not rewritten or len(rewritten) > MAX_REWRITTEN_QUERY_CHARS:
        return original
    if rewritten.lower() in _EMPTY_RESULTS:
        return original
    if _NO_REWRITE_NEEDED.search(rewritten):
        return original
    if rewritten.lower() == original.lower():
        return original

    print(f"[rephrase] {original!r} -> {rewritten!r}")
    return rewritten
