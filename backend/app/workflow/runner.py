"""Entry points for the LangGraph workflow used by the chat API.

Checkpointing is persistent (MongoDB, keyed by conversation id), so multi-turn
slot filling, authentication and confirmations survive restarts. Both the graph
and the checkpointer are built lazily and cached: creating the saver touches
MongoDB (it ensures its indexes) and must not happen at import time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.config import get_settings
from app.services import mongo
from app.services.bank_auth import BankSessionStore
from app.workflow.deps import WorkflowDeps
from app.workflow.graph import build_graph


@dataclass
class WorkflowResult:
    answer: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    rephrased_query: str = ""
    status: str = "ok"


@lru_cache(maxsize=1)
def get_checkpointer() -> Any:
    """Persistent LangGraph checkpointer (one MongoDB-backed saver)."""
    from langgraph.checkpoint.mongodb import MongoDBSaver

    settings = get_settings()
    return MongoDBSaver(
        mongo.get_client(),
        db_name=settings.mongodb_database,
        checkpoint_collection_name="workflow_checkpoints",
        writes_collection_name="workflow_writes",
    )


@lru_cache(maxsize=1)
def get_workflow() -> Any:
    """Compiled workflow graph with the default (real) dependencies."""
    return build_graph(WorkflowDeps.default(), checkpointer=get_checkpointer())


def run_workflow(
    question: str,
    conversation_id: str,
    user_id: str,
    history: list[dict[str, Any]],
) -> WorkflowResult:
    """Run one conversational turn through the workflow."""
    settings = get_settings()
    graph = get_workflow()

    trimmed = history[-settings.max_history_messages :] if history else []
    state = {
        "question": question,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "history": trimmed,
        "sources": [],
    }
    config = {"configurable": {"thread_id": conversation_id}}
    result = graph.invoke(state, config)

    return WorkflowResult(
        answer=result.get("response", "") or "",
        sources=result.get("sources") or [],
        rephrased_query=result.get("rephrased_query") or "",
        status=result.get("status", "ok") or "ok",
    )


def delete_workflow_thread(conversation_id: str) -> None:
    """Best-effort removal of a conversation's checkpoints (and its session)."""
    try:
        get_checkpointer().delete_thread(conversation_id)
    except Exception as exc:  # pragma: no cover - cleanup must not block deletes
        print(f"[workflow] could not delete checkpoint thread: {exc}")

    try:
        BankSessionStore().revoke(conversation_id)
    except Exception as exc:  # pragma: no cover
        print(f"[workflow] could not revoke bank session: {exc}")
