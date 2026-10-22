"""Authentication service: username/password accounts with 24hr sessions.

Data model (MongoDB Atlas):
    users     : {user_id, username, password_hash, created_at}
    sessions  : {token, user_id, created_at, expires_at}   (expires_at has a TTL index)

Every other collection (`files`, `chunks`, `conversations`) is scoped by
`user_id`, so one account can never see another account's data.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt

from app.config import get_settings
from app.services import mongo

# A valid bcrypt hash of a throwaway value. It is used to burn the same amount
# of CPU when the username does not exist, so a missing account cannot be
# detected by timing the response.
_TIMING_EQUALIZER_HASH = "$2b$12$h.938SDGdeUrvPD5fUYy1.COs8INjdiusWX5CdGpuVfYk9bJs.hI."


class AuthError(Exception):
    """Raised for invalid credentials / invalid account data."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish password check; never raises on malformed hashes."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def validate_password(password: str) -> None:
    """Enforce the configured password policy."""
    settings = get_settings()
    if not password:
        raise AuthError("Password is required")
    if len(password) < settings.min_password_length:
        raise AuthError(
            f"Password must be at least {settings.min_password_length} characters"
        )
    if len(password.encode("utf-8")) > settings.max_password_bytes:
        raise AuthError(
            f"Password must be at most {settings.max_password_bytes} bytes long"
        )


def validate_username(username: str) -> str:
    """Normalise and validate a username (stored lowercase)."""
    name = (username or "").strip().lower()
    if not name:
        raise AuthError("Username is required")
    if len(name) < 3:
        raise AuthError("Username must be at least 3 characters")
    if len(name) > 64:
        raise AuthError("Username must be at most 64 characters")
    if not all(ch.isalnum() or ch in "._-" for ch in name):
        raise AuthError("Username may only contain letters, digits, dot, underscore, hyphen")
    return name


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def create_user(username: str, password: str) -> dict[str, Any]:
    """Create an account. Raises AuthError when the username is taken."""
    name = validate_username(username)
    validate_password(password)

    users = mongo.users_collection()
    if users.find_one({"username": name}):
        raise AuthError("Username already exists", status_code=409)

    user = {
        "user_id": str(uuid.uuid4()),
        "username": name,
        "password_hash": hash_password(password),
        "created_at": _utcnow(),
    }
    users.insert_one({**user, "_id": user["user_id"]})
    return {"user_id": user["user_id"], "username": user["username"]}


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Return the public user record when the credentials match, else None."""
    name = (username or "").strip().lower()
    if not name or not password:
        return None

    user = mongo.users_collection().find_one({"username": name})
    if not user:
        # Hash anyway so a missing user and a wrong password take the same time.
        verify_password(password, _TIMING_EQUALIZER_HASH)
        return None
    if not verify_password(password, user.get("password_hash", "")):
        return None
    return {"user_id": user["user_id"], "username": user["username"]}


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def create_session(user_id: str) -> tuple[str, datetime]:
    """Open a session for `user_id`; returns (token, expiry timestamp)."""
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    now = _utcnow()
    expires = now + timedelta(hours=settings.session_ttl_hours)
    mongo.sessions_collection().insert_one(
        {
            "token": token,
            "user_id": user_id,
            "created_at": now,
            "expires_at": expires,
        }
    )
    return token, expires


def get_session(token: str | None) -> dict[str, Any] | None:
    """Return the (valid, unexpired) session document for a token."""
    if not token:
        return None
    sessions = mongo.sessions_collection()
    session = sessions.find_one({"token": token})
    if not session:
        return None

    expires = session.get("expires_at")
    if expires is not None:
        if expires.tzinfo is None:  # defensive: normalise naive datetimes to UTC
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < _utcnow():
            sessions.delete_one({"token": token})
            return None
    return session


def get_current_user(token: str | None) -> dict[str, Any] | None:
    """Resolve a token into the public user record (+ session expiry)."""
    session = get_session(token)
    if not session:
        return None
    user = mongo.users_collection().find_one({"user_id": session["user_id"]})
    if not user:
        return None
    return {
        "user_id": user["user_id"],
        "username": user["username"],
        "expires_at": session.get("expires_at"),
    }


def logout(token: str | None) -> None:
    """Invalidate a session (idempotent)."""
    if not token:
        return
    mongo.sessions_collection().delete_one({"token": token})


def purge_expired_sessions() -> int:
    """Delete sessions that already expired (the TTL index does this too)."""
    result = mongo.sessions_collection().delete_many({"expires_at": {"$lte": _utcnow()}})
    return result.deleted_count
