"""Implicit owner resolution (there is no login any more).

The app is single-user, but every record still carries a `user_id` so the
MongoDB queries and the Atlas vector-search pre-filter keep working unchanged.

Resolution order:
  1. `OWNER_USERNAME` from `.env`, when set (the account is created on demand).
  2. Otherwise the oldest user in the `users` collection - this keeps the data
     of the existing account visible without any configuration.
  3. Otherwise a `local` user is created on first use.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.services import mongo

FALLBACK_USERNAME = "local"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _create_user(username: str) -> dict[str, Any]:
    """Create the owner account (no password - it is never used to sign in).

    A concurrent request may win the race; the unique `username` index then
    raises and the existing document is returned instead.
    """
    user = {
        "user_id": str(uuid.uuid4()),
        "username": username,
        # The email is what the bank-authentication step checks (services/bank_auth.py).
        "email": get_settings().owner_email,
        "created_at": _utcnow(),
    }
    try:
        mongo.users_collection().insert_one({**user, "_id": user["user_id"]})
        return user
    except DuplicateKeyError:
        existing = mongo.users_collection().find_one({"username": username})
        if existing is None:
            raise
        return existing


def get_owner_user() -> dict[str, Any]:
    """Return the implicit owner: `{user_id, username}`.

    Used as a FastAPI dependency by every endpoint, so all data queries are
    scoped to the same account.
    """
    settings = get_settings()
    users = mongo.users_collection()

    configured = (settings.owner_username or "").strip().lower()
    if configured:
        user = users.find_one({"username": configured}) or _create_user(configured)
    else:
        user = users.find_one(sort=[("created_at", 1)]) or _create_user(
            FALLBACK_USERNAME
        )

    # Older accounts (created before bank authentication) have no email. Backfill
    # the configured owner email once so the verification step has something to
    # match; an email that is already set is never overwritten.
    if not (user.get("email") or "").strip():
        settings_email = (settings.owner_email or "").strip().lower()
        if settings_email:
            users.update_one(
                {"user_id": user["user_id"]},
                {"$set": {"email": settings_email}},
            )
            user["email"] = settings_email

    return {
        "user_id": user["user_id"],
        "username": user.get("username") or FALLBACK_USERNAME,
        "email": (user.get("email") or "").strip().lower() or None,
    }
