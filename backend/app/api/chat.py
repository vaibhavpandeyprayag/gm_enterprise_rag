"""Chat endpoint: document RAG and bank operations through the LangGraph workflow."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services import mongo
from app.services.owner import get_owner_user
from app.workflow.runner import run_workflow

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_QUESTION_CHARS = 8000
MAX_TITLE_LEN = 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    title: str
    user_message: dict
    assistant_message: dict


def _derive_title(message: str) -> str:
    """Local, cheap title derivation (no LLM call)."""
    compact = " ".join(message.split())
    return compact if len(compact) <= MAX_TITLE_LEN else compact[: MAX_TITLE_LEN - 1] + "…"


@router.post("", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    user: dict = Depends(get_owner_user),
) -> ChatResponse:
    """Answer a message, persisting both turns in the conversation."""
    collections = mongo.collections_collection()
    user_id = user["user_id"]

    # ---- Resolve or create the conversation -----------------------------
    if body.conversation_id:
        conversation = collections.find_one(
            {"conversation_id": body.conversation_id, "user_id": user_id}
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        now = _utcnow()
        conversation = {
            "conversation_id": str(uuid.uuid4()),
            "title": _derive_title(body.message),
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "user_id": user_id,
        }
        collections.insert_one({**conversation, "_id": conversation["conversation_id"]})

    history = conversation.get("messages", [])

    # ---- Workflow turn (routing, bank auth/ops, or document RAG) ---------
    # The conversation id doubles as the workflow thread id, so pending
    # questions (slots, OTP, confirmation) are restored from the checkpoint.
    try:
        result = run_workflow(
            question=body.message,
            conversation_id=conversation["conversation_id"],
            user_id=user_id,
            history=history,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM/provider error: {exc}",
        ) from exc

    # ---- Persist both turns ---------------------------------------------
    now = _utcnow()
    user_message = {
        "role": "user",
        "content": body.message,
        "created_at": now,
        "sources": [],
        # The standalone query that was used for the retrieval - it is what the
        # history shows to the LLM in the following turns.
        "rephrased": result.rephrased_query,
    }
    assistant_message = {
        "role": "assistant",
        "content": result.answer,
        "created_at": _utcnow(),
        "sources": result.sources,
    }
    # `result.status` is informational only (ok / needs_auth / needs_input /
    # needs_confirmation / cancelled). The reply text already asks the user for
    # whatever the workflow needs next.

    collections.update_one(
        {"conversation_id": conversation["conversation_id"], "user_id": user_id},
        {
            "$push": {"messages": {"$each": [user_message, assistant_message]}},
            "$set": {"updated_at": now},
        },
    )

    return ChatResponse(
        conversation_id=conversation["conversation_id"],
        title=conversation["title"],
        user_message=user_message,
        assistant_message=assistant_message,
    )
