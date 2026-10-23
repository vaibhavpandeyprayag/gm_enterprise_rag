"""User-facing fallbacks when retrieval or generation fails (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import rag


def test_friendly_refusal_is_recognised():
    answer = "I couldn't find anything about that in your uploaded documents."
    assert rag._is_refusal(answer)


def _patch_pipeline(monkeypatch, search):
    monkeypatch.setattr(
        rag,
        "decompose_query",
        lambda question, history, settings: [question],
    )
    monkeypatch.setattr(
        rag, "rephrase_query", lambda query, history, settings: query
    )
    monkeypatch.setattr(rag, "similarity_search", search)


def test_retrieval_outage_returns_friendly_answer(monkeypatch):
    def failing_search(*args, **kwargs):
        raise RuntimeError("index unavailable")

    _patch_pipeline(monkeypatch, failing_search)
    monkeypatch.setattr(
        rag, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("no LLM call"))
    )

    result = rag.answer_question("What is this?", [], "user-1")

    assert result.answer == rag.RETRIEVAL_UNAVAILABLE_ANSWER
    assert result.sources == []
    assert result.rephrased_query == "What is this?"


def test_generation_failure_returns_friendly_answer(monkeypatch):
    _patch_pipeline(monkeypatch, lambda *args, **kwargs: [])

    def failing_invoke(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        rag, "get_llm", lambda: SimpleNamespace(invoke=failing_invoke)
    )

    result = rag.answer_question("What is this?", [], "user-1")

    assert result.answer == rag.GENERATION_UNAVAILABLE_ANSWER
    assert result.sources == []


def test_empty_model_answer_returns_friendly_answer(monkeypatch):
    _patch_pipeline(monkeypatch, lambda *args, **kwargs: [])
    monkeypatch.setattr(
        rag,
        "get_llm",
        lambda: SimpleNamespace(invoke=lambda messages: SimpleNamespace(content="")),
    )

    result = rag.answer_question("What is this?", [], "user-1")

    assert result.answer == rag.GENERATION_UNAVAILABLE_ANSWER
    assert result.sources == []
