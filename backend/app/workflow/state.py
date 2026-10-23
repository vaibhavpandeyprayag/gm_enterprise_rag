"""Shared LangGraph state for the RAG + banking workflow.

All values are JSON-serialisable because checkpoints are persisted in MongoDB.
Authorization is *not* derived from this state: the server-side session store is
consulted every time a bank tool runs.
"""

from __future__ import annotations

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    # ---- request --------------------------------------------------------
    question: str
    history: list[dict[str, Any]]
    user_id: str  # owner of the conversation (single-user mode)
    conversation_id: str  # also the LangGraph thread id

    # ---- routing --------------------------------------------------------
    intent: str | None
    operation: str | None  # currently selected bank task
    status: str  # ok | needs_auth | needs_input | needs_confirmation | cancelled | error

    # ---- authorization --------------------------------------------------
    auth_stage: str | None  # "email" | "otp"
    auth_email: str | None
    auth_user_id: str | None
    verified_user_id: str | None
    otp_attempts: int
    resume_to: str | None  # "slots" | "execute" after successful OTP
    pending_operation: str | None
    pending_action: str | None  # "start" | "execute"

    # ---- slot filling / confirmation -----------------------------------
    collected: dict[str, Any]
    awaiting_field: str | None
    confirmation_pending: bool
    confirmed: bool
    execution_key: str | None  # stable idempotency key for one execution
    abandoned: bool  # the user changed topic; route the message fresh

    # ---- output ---------------------------------------------------------
    response: str
    sources: list[dict[str, Any]]
    rephrased_query: str
    error: str | None
