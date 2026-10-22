"""Injectable dependencies for the workflow (real services by default).

Tests build the graph with fakes here, so the suite never calls Groq or MongoDB
and can control session expiry deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.config import get_settings
from app.services import bank_auth, rag
from app.services.bank_auth import BankSessionStore
from app.services.bank_tools import BankTools
from app.services.intent_router import classify_intent
from app.services.slot_extraction import classify_confirmation, extract_field


@dataclass
class WorkflowDeps:
    sessions: Any
    tools: Any
    classify_intent: Callable[..., str]
    extract_field: Callable[..., Any]
    classify_confirmation: Callable[..., str]
    answer_question: Callable[..., Any]
    find_user_by_email: Callable[[str], dict[str, Any] | None]
    otp_code: str
    ttl_minutes: int
    settings: Any

    @classmethod
    def default(cls) -> "WorkflowDeps":
        settings = get_settings()
        sessions = BankSessionStore()
        return cls(
            sessions=sessions,
            tools=BankTools(sessions),
            classify_intent=classify_intent,
            extract_field=extract_field,
            classify_confirmation=classify_confirmation,
            answer_question=rag.answer_question,
            find_user_by_email=bank_auth.find_user_by_email,
            otp_code=settings.bank_otp_code,
            ttl_minutes=settings.bank_session_ttl_minutes,
            settings=settings,
        )
