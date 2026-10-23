"""Shared test fixtures: fake banking/session/LLM dependencies (no network).

The workflow is built with `InMemorySaver`, so tests never touch MongoDB and
never call Groq.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

# Make `app` importable when pytest is run from `backend/` without `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.bank_auth import SessionRequired  # noqa: E402
from app.services.bank_tasks import DOCUMENT_QA  # noqa: E402
from app.workflow.deps import WorkflowDeps  # noqa: E402
from app.workflow.graph import build_graph  # noqa: E402

CONVERSATION_ID = "conv-1"
USER_ID = "user-1"
EMAIL = "owner@example.com"
OTP = "123456"


class FakeSessions:
    """In-memory stand-in for `BankSessionStore` with a global expiry switch."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.expired = False

    def create(
        self, user_id: str, conversation_id: str, ttl_minutes: int | None = None
    ) -> dict[str, Any]:
        doc = {"user_id": user_id, "conversation_id": conversation_id}
        self.sessions[conversation_id] = doc
        self.expired = False
        return doc

    def get(
        self, conversation_id: str, user_id: str | None = None
    ) -> dict[str, Any] | None:
        doc = self.sessions.get(conversation_id)
        if doc is None or self.expired:
            return None
        if user_id is not None and doc.get("user_id") != user_id:
            return None
        return doc

    def require(
        self, conversation_id: str, user_id: str | None = None
    ) -> dict[str, Any]:
        doc = self.get(conversation_id, user_id=user_id)
        if doc is None:
            raise SessionRequired("session missing or expired")
        return doc

    def revoke(self, conversation_id: str) -> None:
        self.sessions.pop(conversation_id, None)

    def expire(self) -> None:
        self.expired = True


class FakeTools:
    """Records every tool call; enforces the same session check as production."""

    def __init__(self, sessions: FakeSessions) -> None:
        self.sessions = sessions
        self.calls: list[dict[str, Any]] = []

    def get_account_balance(
        self, *, user_id: str, conversation_id: str, execution_key: str = ""
    ) -> dict[str, Any]:
        self.sessions.require(conversation_id, user_id=user_id)
        self.calls.append({"action": "balance", "execution_key": execution_key})
        return {
            "account": {"bank_name": "Test Bank", "last4": "1234"},
            "balance": 1234.5,
            "currency": "USD",
        }

    def get_last_transactions(
        self, *, user_id: str, conversation_id: str, execution_key: str = ""
    ) -> dict[str, Any]:
        self.sessions.require(conversation_id, user_id=user_id)
        self.calls.append({"action": "transactions", "execution_key": execution_key})
        return {
            "account": {"bank_name": "Test Bank", "last4": "1234"},
            "transactions": [],
            "currency": "USD",
        }

    def issue_chequebook(
        self,
        *,
        user_id: str,
        conversation_id: str,
        execution_key: str,
        leaf_count: int,
        delivery_preference: str,
    ) -> dict[str, Any]:
        self.sessions.require(conversation_id, user_id=user_id)
        self.calls.append(
            {
                "action": "chequebook",
                "execution_key": execution_key,
                "leaf_count": leaf_count,
                "delivery_preference": delivery_preference,
            }
        )
        return {
            "request": {
                "request_id": "req-chequebook",
                "leaf_count": leaf_count,
                "delivery_preference": delivery_preference,
            }
        }

    def block_debit_card(
        self, *, user_id: str, conversation_id: str, execution_key: str = ""
    ) -> dict[str, Any]:
        self.sessions.require(conversation_id, user_id=user_id)
        self.calls.append({"action": "block", "execution_key": execution_key})
        return {"card": {"last4": "1234"}, "already_blocked": False}


class Fakes:
    """Controls intent/extraction/confirmation and captures document answers."""

    def __init__(self) -> None:
        self.sessions = FakeSessions()
        self.tools = FakeTools(self.sessions)
        self.intent = DOCUMENT_QA
        self.slot_value: str | None = None
        self.slot_action = "provide"
        self.confirmation = "confirm"
        # Whether the latest message answers the paused prompt or changes topic.
        self.pending_reply = "answer_pending"
        self.doc_answer = "Answer from your documents."
        self.users = {EMAIL: {"user_id": USER_ID, "username": "owner"}}

    def deps(self) -> WorkflowDeps:
        return WorkflowDeps(
            sessions=self.sessions,
            tools=self.tools,
            classify_intent=lambda question, history, settings: self.intent,
            extract_field=self._extract_field,
            classify_confirmation=lambda question, history, settings: (
                self.confirmation
            ),
            classify_pending_reply=lambda pending, question, history, settings: (
                self.pending_reply
            ),
            answer_question=self._answer_question,
            find_user_by_email=lambda email: self.users.get(email),
            otp_code=OTP,
            ttl_minutes=15,
            settings=SimpleNamespace(max_history_messages=10),
        )

    def _extract_field(self, field: Any, question: str, history: Any, settings: Any):
        value = self.slot_value if self.slot_value is not None else question
        return SimpleNamespace(action=self.slot_action, value=value)

    def _answer_question(self, question: str, history: Any, user_id: str):
        return SimpleNamespace(
            answer=self.doc_answer,
            sources=[{"file_id": "f1", "file_name": "doc.pdf"}],
            rephrased_query="standalone question",
        )


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def graph(fakes: Fakes):
    return build_graph(fakes.deps(), checkpointer=InMemorySaver())


def send(
    graph: Any,
    text: str,
    conversation_id: str = CONVERSATION_ID,
    user_id: str = USER_ID,
) -> dict[str, Any]:
    """Send one turn through the workflow thread."""
    return graph.invoke(
        {
            "question": text,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "history": [],
            "sources": [],
        },
        {"configurable": {"thread_id": conversation_id}},
    )


def authenticate(graph: Any) -> dict[str, Any]:
    """Complete the email + OTP steps for a pending bank request."""
    assert send(graph, EMAIL)["auth_stage"] == "otp"
    return send(graph, OTP)
