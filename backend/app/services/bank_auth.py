"""Conversation-scoped bank authentication and authorization.

Bank operations need a short-lived, *server-side* authorization that is checked
in Python before any data is read or written. The graph state only records that
the conversation is in an auth step - it never grants access by itself.

Flow (implemented by the LangGraph workflow, see app/workflow/graph.py):
  1. The user is asked for the email registered on their account.
  2. The email is matched against the `users` collection.
  3. A dummy OTP (config `BANK_OTP_CODE`, development only) is checked.
  4. On success a session is stored in MongoDB, scoped to the conversation and
     the verified user, and expires after `BANK_SESSION_TTL_MINUTES` (15 min).

The session store is intentionally independent of the conversation/checkpoint
history: deleting or editing history cannot create an authorization.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import get_settings
from app.services import mongo

# Deliberately stricter than "anything@anything.anything": stops at punctuation
# so "My email is vp5@xyz.com." yields "vp5@xyz.com" (not the trailing dot).
_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)


class BankAuthError(Exception):
    """Bank authorization failed (missing, expired, or for another user)."""


class SessionRequired(BankAuthError):
    """No valid authorization session exists for this conversation."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def extract_email(text: str) -> str | None:
    """Return the first email address in a user message, normalised."""
    match = _EMAIL_PATTERN.search(text or "")
    return normalize_email(match.group(0)) if match else None


def find_user_by_email(email: str) -> dict[str, Any] | None:
    """Match a registered email against the `users` collection."""
    normalized = normalize_email(email)
    if not normalized:
        return None
    user = mongo.users_collection().find_one({"email": normalized})
    if user is None:
        return None
    return {
        "user_id": user["user_id"],
        "username": user.get("username", ""),
        "email": normalized,
    }


def _as_utc(value: datetime) -> datetime:
    """The Mongo client is tz-aware, but normalise naive values defensively."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class BankSessionStore:
    """MongoDB-backed authorization sessions (server-side, never LLM-provided)."""

    def create(
        self,
        user_id: str,
        conversation_id: str,
        *,
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Create (or replace) the session for a conversation."""
        settings = get_settings()
        minutes = ttl_minutes or settings.bank_session_ttl_minutes
        now = _utcnow()
        document = {
            "session_id": secrets.token_urlsafe(24),
            "user_id": user_id,
            "conversation_id": conversation_id,
            "created_at": now,
            "expires_at": now + timedelta(minutes=minutes),
        }
        mongo.bank_sessions_collection().update_one(
            {"conversation_id": conversation_id},
            {"$set": document},
            upsert=True,
        )
        return document

    def get(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a valid session, or None when missing/expired/for another user."""
        doc = mongo.bank_sessions_collection().find_one(
            {"conversation_id": conversation_id}
        )
        if doc is None:
            return None

        expires = doc.get("expires_at")
        if expires is not None and _as_utc(expires) <= _utcnow():
            # Expired sessions are removed eagerly (the TTL index is a backstop).
            self.revoke(conversation_id)
            return None

        if user_id is not None and doc.get("user_id") != user_id:
            return None
        return doc

    def require(
        self,
        conversation_id: str,
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the session or raise `SessionRequired`.

        Every bank tool calls this immediately before touching data, so an
        expired session can never be bypassed by graph state or the model.
        """
        session = self.get(conversation_id, user_id=user_id)
        if session is None:
            raise SessionRequired(
                "Your secure bank session is missing or has expired. "
                "Please verify your registered email again."
            )
        return session

    def revoke(self, conversation_id: str) -> None:
        """Delete a conversation's session (idempotent)."""
        mongo.bank_sessions_collection().delete_one(
            {"conversation_id": conversation_id}
        )
