"""Shared FastAPI dependencies (authentication)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request

from app.config import get_settings
from app.services import auth


def extract_token(request: Request) -> str | None:
    """Read the session token from the cookie, falling back to a Bearer header.

    The cookie is the primary mechanism (browser sessions survive a restart);
    the Bearer header exists for API clients (curl, mobile apps, tests).
    """
    token = request.cookies.get(get_settings().session_cookie_name)
    if token:
        return token
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    return None


def get_current_user(request: Request) -> dict[str, Any]:
    """Resolve the caller from the session token.

    Raises 401 when there is no valid session - use it as a route dependency to
    make an endpoint authenticated.
    """
    token = extract_token(request)
    user = auth.get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
