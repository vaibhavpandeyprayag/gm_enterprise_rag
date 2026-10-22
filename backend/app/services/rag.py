"""RAG pipeline: retrieve relevant chunks -> prompt -> LLM answer -> citations.

The assistant answers only from the ingested sources. One LLM call per turn,
plus an optional query-rephrasing call for context-dependent follow-ups.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_settings
from app.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from app.services.history import history_text
from app.services.llm import content_to_text, get_llm
from app.services.query_rewriter import rephrase_query
from app.services.vectorstore import similarity_search


@dataclass
class RagResult:
    """Structured answer returned to the API layer."""

    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    # The standalone query that was used for the retrieval (equal to the
    # original question when no rephrasing was needed).
    rephrased_query: str = ""


def _locator(metadata: dict) -> str:
    """Human-readable position of a chunk inside its file (page/slide/sheet/...)."""
    for key in ("page", "slide", "sheet", "table", "section", "rows"):
        if key in metadata:
            return f"{key}: {metadata[key]}"
    return ""


def _content_to_text(content: Any) -> str:
    """Normalise chat-model output (str or content-block list) to plain text."""
    return content_to_text(content)


# Matches inline citation markers: ASCII [1] or full-width 【1】 (1-2 digits).
_CITATION_PATTERN = re.compile(r"[\[【](\d{1,2})[\]】]")

FALLBACK_SOURCE_COUNT = 3

# Phrases that mean "the documents had nothing useful" - in that case no source
# should be shown (otherwise a refusal looks like it was grounded in a file).
_REFUSAL_MARKERS = (
    "could not find",
    "couldn't find",
    "cannot find",
    "can't find",
    "not in the available sources",
    "no matching documents",
)


def _is_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def _cited_sources(
    answer: str,
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Return (sources, answer) with citations aligned and re-numbered.

    Only the sources the answer actually cites are kept, and they are remapped
    so the displayed numbering matches the citations in the text exactly (no
    gaps, no unused entries). If the model cites nothing, the first few
    retrieved sources are shown - and the answer's numbers, if any, remapped.

    A refusal ("I could not find that...") returns no sources.
    """
    if _is_refusal(answer):
        return [], answer

    cited_all = [int(m) for m in _CITATION_PATTERN.findall(answer)]
    # Distinct candidates in order of first appearance, restricted to real sources.
    valid = {s["index"] for s in sources}
    order: list[int] = []
    for number in cited_all:
        if number in valid and number not in order:
            order.append(number)

    # Nothing cited (or only bogus numbers): fall back to the top retrieved hits.
    # if not order:
    #     order = [s["index"] for s in sources[:FALLBACK_SOURCE_COUNT]]
    #     if not order:
    #         return [], answer

    # old index -> new sequential index (1, 2, 3, ...)
    remap = {old: new for new, old in enumerate(order, start=1)}
    by_index = {s["index"]: s for s in sources}
    renumbered = [{**by_index[old], "index": remap[old]} for old in order]

    # Rewrite the citation numbers in the answer to match the new numbering.
    def _replace(match: re.Match) -> str:
        old = int(match.group(1))
        return f"[{remap[old]}]" if old in remap else match.group(0)

    answer = _CITATION_PATTERN.sub(_replace, answer)
    return renumbered, answer


def _add_source(
    sources: list[dict[str, Any]],
    context_parts: list[str],
    document: Any,
) -> int:
    """Append one retrieved chunk as a numbered source; return chars added."""
    metadata = document.metadata or {}
    index = len(sources) + 1
    entry = {
        "index": index,
        "kind": "url" if metadata.get("url") else "document",
        "source": metadata.get("source", "unknown"),
        "locator": _locator(metadata),
        "snippet": document.page_content[:300],
    }
    if metadata.get("url"):
        entry["url"] = metadata["url"]
    sources.append(entry)
    header = f"[{index}] {entry['source']}" + (
        f" ({entry['locator']})" if entry["locator"] else ""
    )
    block = f"{header}\n{document.page_content}"
    context_parts.append(block)
    return len(block)


def _retrieve(
    question: str, settings: Any, user_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Top-k most similar chunks across the user's own ingested sources."""
    sources: list[dict[str, Any]] = []
    context_parts: list[str] = []
    context_chars = 0

    try:
        hits = similarity_search(question, k=settings.retrieval_top_k, user_id=user_id)
    except Exception as exc:
        print(f"[rag] vector search failed: {exc}")
        hits = []

    for document, score in hits:
        if score < settings.retrieval_min_score:
            continue
        if context_chars >= settings.max_context_chars:
            break
        context_chars += _add_source(sources, context_parts, document)

    return sources, context_parts


def answer_question(question: str, history: list[dict], user_id: str) -> RagResult:
    """Answer `question` grounded in the sources the user has ingested."""
    settings = get_settings()

    # ---- 1. Make the query standalone -------------------------------------
    # A context-dependent follow-up ("what about its pricing?") is rewritten
    # into a self-contained query; a clear question is passed through as-is.
    # On any failure the original question is used.
    
    search_query = rephrase_query(question, history, settings)
    

    # ---- 2. Retrieve the most relevant chunks -----------------------------
    sources, context_parts = _retrieve(search_query, settings, user_id)
    context = "\n\n".join(context_parts) or "(no matching documents found)"

    # ---- 3. Prompt assembly -----------------------------------------------
    user_prompt = USER_PROMPT_TEMPLATE.format(
        context=context,
        history=history_text(history, settings.max_history_messages),
        question=question,
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    # print("user_prompt >>", user_prompt)
    # ---- 4. LLM generation ------------------------------------------------
    response = get_llm().invoke(messages)
    answer = _content_to_text(response.content).strip()

    # print("==========================")
    # print("answer >>", answer)
    # print("==========================")
    # print("sources >>", sources)
    # print("==========================")
    # print("context_parts >>", context_parts)
    # print("==========================")
    # ---- 5. Align sources with the citations the answer actually uses ----
    final_sources, answer = _cited_sources(answer, sources)
    return RagResult(
        answer=answer,
        sources=final_sources,
        rephrased_query=search_query,
    )
