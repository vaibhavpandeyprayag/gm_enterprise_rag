"""Conversation endpoints - multiple chats with full message history.

Every conversation belongs to the authenticated user (`user_id`); all queries
are filtered by it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services import mongo

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime
    sources: list[dict] = Field(default_factory=list)
    # User turns only: the standalone (rephrased) query used for retrieval.
    rephrased: str | None = None


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class ConversationDetail(ConversationSummary):
    messages: list[Message] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    title: str = "New chat"


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _summary(doc: dict) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=doc["conversation_id"],
        title=doc.get("title", "New chat"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        message_count=len(doc.get("messages", [])),
    )


def _get_doc(conversation_id: str, user_id: str) -> dict:
    doc = mongo.collections_collection().find_one(
        {"conversation_id": conversation_id, "user_id": user_id}
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return doc


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    user: dict = Depends(get_current_user),
) -> list[ConversationSummary]:
    """All conversations of the current user, most recently active first."""
    cursor = (
        mongo.collections_collection()
        .find({"user_id": user["user_id"]})
        .sort("updated_at", -1)
    )
    return [_summary(doc) for doc in cursor]


@router.post("", response_model=ConversationSummary)
def create_conversation(
    user: dict = Depends(get_current_user),
    body: CreateConversationRequest | None = None,
) -> ConversationSummary:
    """Create an empty conversation (the chat endpoint can do this implicitly)."""
    now = _utcnow()
    doc = {
        "conversation_id": str(uuid.uuid4()),
        "title": (body.title if body else "New chat").strip() or "New chat",
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "user_id": user["user_id"],
    }
    mongo.collections_collection().insert_one({**doc, "_id": doc["conversation_id"]})
    return _summary(doc)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
) -> ConversationDetail:
    """Full conversation including every message and its sources."""
    doc = _get_doc(conversation_id, user["user_id"])
    summary = _summary(doc)
    return ConversationDetail(**summary.model_dump(), messages=doc.get("messages", []))


@router.patch("/{conversation_id}", response_model=ConversationSummary)
def rename_conversation(
    conversation_id: str,
    body: RenameConversationRequest,
    user: dict = Depends(get_current_user),
) -> ConversationSummary:
    """Rename a chat."""
    user_id = user["user_id"]
    mongo.collections_collection().update_one(
        {"conversation_id": conversation_id, "user_id": user_id},
        {"$set": {"title": body.title.strip(), "updated_at": _utcnow()}},
    )
    return _summary(_get_doc(conversation_id, user_id))


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
) -> dict[str, str]:
    """Delete a chat (ingested files stay untouched)."""
    result = mongo.collections_collection().delete_one(
        {"conversation_id": conversation_id, "user_id": user["user_id"]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation_id": conversation_id, "status": "deleted"}
