"""Authentication endpoints: register, login, session check, logout.

The session token is stored in an HttpOnly cookie, so page scripts cannot read
it. It is never echoed back in the response body.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import extract_token
from app.config import get_settings
from app.services import auth
from app.services.auth import AuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    user_id: str
    username: str


class AuthResponse(BaseModel):
    user: UserOut
    expires_at: datetime | None = None
    # The session token itself is never returned in the body - it lives only
    # in the HttpOnly cookie, so page scripts cannot read it.


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _set_session_cookie(response: Response, token: str, max_age: int) -> None:
    """Write the session cookie using the configured policy."""
    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        max_age=max_age,
        path="/",
        domain=settings.session_cookie_domain,
    )


def _start_session(response: Response, user: dict[str, Any]) -> AuthResponse:
    """Create a session and attach its cookie to the response."""
    settings = get_settings()
    token, expires = auth.create_session(user["user_id"])
    _set_session_cookie(response, token, max_age=settings.session_ttl_hours * 3600)
    return AuthResponse(
        user=UserOut(**user),
        expires_at=expires,
    )


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.post("/register", response_model=AuthResponse)
def register(body: Credentials, response: Response) -> AuthResponse:
    """Create an account and open a session for it."""
    try:
        user = auth.create_user(body.username, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return _start_session(response, user)


@router.post("/login", response_model=AuthResponse)
def login(body: Credentials, response: Response) -> AuthResponse:
    """Verify the credentials and open a session."""
    user = auth.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return _start_session(response, user)


@router.get("/me", response_model=AuthResponse)
def me(request: Request) -> AuthResponse:
    """Return the current session (used by the UI on startup)."""
    token = extract_token(request)
    user = auth.get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return AuthResponse(
        user=UserOut(user_id=user["user_id"], username=user["username"]),
        expires_at=user.get("expires_at"),
    )


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    """Invalidate the session and clear the cookie."""
    settings = get_settings()
    token = extract_token(request)
    auth.logout(token)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        domain=settings.session_cookie_domain,
    )
    return {"ok": True}


@router.get("/session-info")
def session_info() -> dict[str, Any]:
    """Session policy (TTL, cookie name) - useful for the client."""
    settings = get_settings()
    return {
        "ttl_hours": settings.session_ttl_hours,
        "cookie_name": settings.session_cookie_name,
        "secure": settings.session_cookie_secure,
    }
