"""RAG pipeline: decompose -> retrieve -> rerank -> prompt -> LLM answer.

The assistant answers only from the ingested sources. Multi-part questions are
split into standalone subqueries, each subquery is retrieved and reranked, and
the deduplicated chunks are answered against the original question.
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
from app.services.query_decomposition import decompose_query
from app.services.query_rewriter import rephrase_query
from app.services.reranker import rerank
from app.services.vectorstore import similarity_search


@dataclass
class RagResult:
    """Structured answer returned to the API layer."""

    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    # The standalone query (or "; "-joined subqueries) actually used for the
    # retrieval; equal to the original question when nothing was rewritten.
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

# User-facing fallbacks when a pipeline step fails before the model can answer.
RETRIEVAL_UNAVAILABLE_ANSWER = (
    "I couldn't search your documents just now, so I wasn't able to check your "
    "files. Please try again in a moment."
)
GENERATION_UNAVAILABLE_ANSWER = (
    "I'm sorry - I couldn't put together an answer just now. Please try again "
    "in a moment."
)

# Phrases that mean "the documents had nothing useful" - in that case no source
# should be shown (otherwise a refusal looks like it was grounded in a file).
_REFUSAL_MARKERS = (
    "could not find",
    "couldn't find",
    "cannot find",
    "can't find",
    "unable to find",
    "unable to answer",
    "not in the available sources",
    "no matching documents",
    "do not have any information",
    "don't have any information",
    "no information about",
    "not mentioned in the available sources",
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

    A refusal ("I couldn't find anything about that...") returns no sources.
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


def _chunk_key(document: Any) -> tuple:
    """Identity of a chunk (the same chunk can come back for several subqueries)."""
    metadata = document.metadata or {}
    identifier = metadata.get("_id")
    if identifier is not None:
        return ("id", str(identifier))
    return ("content", metadata.get("source"), document.page_content)


def _select_documents(
    queries: list[str], settings: Any, user_id: str
) -> tuple[list[Any], bool]:
    """Retrieve and rerank each subquery, then deduplicate the chunks.

    Also reports whether *every* retrieval attempt failed, so the caller can
    return a clear "index unavailable" message instead of a misleading
    "nothing found in your documents" answer.
    """
    selected: list[Any] = []
    seen: set[tuple] = set()
    failures = 0

    for query in queries:
        try:
            hits = similarity_search(
                query, k=settings.retrieval_top_k, user_id=user_id
            )
        except Exception as exc:
            failures += 1
            print(f"[rag] vector search failed for {query!r}: {exc}")
            continue

        candidates = [
            document
            for document, score in hits
            if score >= settings.retrieval_min_score
        ]
        if settings.rerank_enabled:
            chosen = rerank(query, candidates, settings.rerank_top_n)
        else:
            chosen = candidates[: settings.rerank_top_n]

        for document in chosen:
            key = _chunk_key(document)
            if key in seen:
                continue
            seen.add(key)
            selected.append(document)

    all_failed = bool(queries) and failures == len(queries)
    return selected, all_failed


def _build_context(
    documents: list[Any], settings: Any
) -> tuple[list[dict[str, Any]], list[str]]:
    """Number the selected chunks and cap the context size."""
    sources: list[dict[str, Any]] = []
    context_parts: list[str] = []
    context_chars = 0

    for document in documents:
        if context_chars >= settings.max_context_chars:
            break
        context_chars += _add_source(sources, context_parts, document)

    return sources, context_parts


def answer_question(question: str, history: list[dict], user_id: str) -> RagResult:
    """Answer `question` grounded in the sources the user has ingested."""
    settings = get_settings()

    # ---- 1. Split the question into standalone subqueries -----------------
    subqueries = decompose_query(question, history, settings)
    # print("===============================", "\n")
    # print("subqueries >>", subqueries, "\n")
    # ---- 2. Rephrase each subquery if needed ------------------------------
    # The existing rephraser decides: a standalone subquery is passed through
    # unchanged, and an LLM failure falls back to the subquery itself.
    search_queries: list[str] = []
    seen_queries: set[str] = set()
    for subquery in subqueries:
        query = rephrase_query(subquery, history, settings)
        if query.casefold() in seen_queries:
            continue
        seen_queries.add(query.casefold())
        search_queries.append(query)

    # ---- 3. Retrieve + rerank per subquery, deduplicate -------------------
    # print("===============================", "\n")
    # print("search_queries >>", search_queries, "\n")
    documents, retrieval_failed = _select_documents(
        search_queries, settings, user_id
    )
    if retrieval_failed:
        print("[rag] every retrieval attempt failed; returning an unavailable notice")
        return RagResult(
            answer=RETRIEVAL_UNAVAILABLE_ANSWER,
            sources=[],
            rephrased_query="; ".join(search_queries),
        )

    # ---- 4. Number the combined context -----------------------------------
    sources, context_parts = _build_context(documents, settings)
    context = "\n\n".join(context_parts) or "(no matching documents found)"

    # ---- 5. Prompt assembly (original question + combined context) --------
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
    # ---- 6. LLM generation ------------------------------------------------
    # Provider failures and empty replies return a friendly fallback answer
    # instead of surfacing a raw 5xx error to the chat UI.
    try:
        response = get_llm().invoke(messages)
        answer = _content_to_text(response.content).strip()
    except Exception as exc:
        print(f"[rag] answer generation failed: {exc}")
        answer = ""

    if not answer:
        print("[rag] no answer generated; returning the friendly fallback")
        return RagResult(
            answer=GENERATION_UNAVAILABLE_ANSWER,
            sources=[],
            rephrased_query="; ".join(search_queries),
        )

    # print("==========================")
    # print("answer >>", answer)
    # print("==========================")
    # print("sources >>", sources)
    # print("==========================")
    # print("context_parts >>", context_parts)
    # print("==========================")
    # ---- 7. Align sources with the citations the answer actually uses ----
    final_sources, answer = _cited_sources(answer, sources)
    return RagResult(
        answer=answer,
        sources=final_sources,
        rephrased_query="; ".join(search_queries),
    )
