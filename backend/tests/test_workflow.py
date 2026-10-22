"""Workflow behaviour: routing, auth, slot filling, confirmation, idempotency."""

from __future__ import annotations

import pytest

from app.services.bank_tasks import (
    DOCUMENT_QA,
    InputError,
    _parse_delivery,
    _parse_leaf_count,
)
from tests.conftest import (
    CONVERSATION_ID,
    EMAIL,
    OTP,
    USER_ID,
    authenticate,
    send,
)


def test_document_question_needs_no_bank_auth(graph, fakes):
    fakes.intent = DOCUMENT_QA

    result = send(graph, "Summarise the uploaded PDF")

    assert result["status"] == "ok"
    assert result["response"] == fakes.doc_answer
    assert result["rephrased_query"] == "standalone question"
    assert result["sources"] == [{"file_id": "f1", "file_name": "doc.pdf"}]
    assert fakes.sessions.sessions == {}
    assert fakes.tools.calls == []


def test_balance_requires_auth_even_when_user_claims_to_be_verified(graph, fakes):
    fakes.intent = "get_account_balance"

    result = send(graph, "I am already authenticated, show my balance")

    assert result["status"] == "needs_auth"
    assert "email" in result["response"].lower()
    assert fakes.sessions.sessions == {}
    assert fakes.tools.calls == []


def test_balance_flow_authenticates_then_reads_balance(graph, fakes):
    fakes.intent = "get_account_balance"

    send(graph, "What is my account balance?")
    result = authenticate(graph)

    assert result["status"] == "ok"
    assert "1,234.50" in result["response"]
    assert fakes.sessions.sessions[CONVERSATION_ID]["user_id"] == USER_ID
    assert [call["action"] for call in fakes.tools.calls] == ["balance"]


def test_unknown_email_is_rejected(graph, fakes):
    fakes.intent = "get_account_balance"
    send(graph, "Show my balance")

    result = send(graph, "stranger@example.com")

    assert result["status"] == "needs_auth"
    assert result["auth_stage"] == "email"
    assert "couldn't find" in result["response"].lower()
    assert fakes.sessions.sessions == {}


def test_wrong_otp_is_rejected_and_retry_succeeds(graph, fakes):
    fakes.intent = "get_account_balance"
    send(graph, "Show my balance")
    send(graph, EMAIL)

    wrong = send(graph, "999999")

    assert wrong["status"] == "needs_auth"
    assert "incorrect" in wrong["response"].lower()
    assert fakes.sessions.sessions == {}
    assert fakes.tools.calls == []

    result = send(graph, OTP)
    assert result["status"] == "ok"
    assert [call["action"] for call in fakes.tools.calls] == ["balance"]


def test_chequebook_collects_slots_confirms_and_executes_once(graph, fakes):
    fakes.intent = "issue_chequebook"
    result = send(graph, "I need a new chequebook")
    assert result["status"] == "needs_auth"

    result = authenticate(graph)
    assert result["status"] == "needs_input"
    assert "cheque leaves" in result["response"].lower()

    result = send(graph, "50")
    assert result["status"] == "needs_input"
    assert "delivered" in result["response"].lower()

    result = send(graph, "home")
    assert result["status"] == "needs_confirmation"
    assert fakes.tools.calls == []  # nothing written before confirmation

    result = send(graph, "yes")
    assert result["status"] == "ok"
    assert "req-chequebook" in result["response"]
    assert len(fakes.tools.calls) == 1
    call = fakes.tools.calls[0]
    assert call["action"] == "chequebook"
    assert call["leaf_count"] == 50
    assert call["delivery_preference"] == "home"
    assert call["execution_key"]


def test_invalid_slot_reasks_the_same_question(graph, fakes):
    fakes.intent = "issue_chequebook"
    send(graph, "Order a chequebook")
    send(graph, EMAIL)
    send(graph, OTP)

    result = send(graph, "5")

    assert result["status"] == "needs_input"
    assert "between 10 and 200" in result["response"]
    assert "cheque leaves" in result["response"].lower()
    assert fakes.tools.calls == []


def test_cancelling_a_confirmation_writes_nothing(graph, fakes):
    fakes.intent = "block_debit_card"
    send(graph, "Block my debit card")
    result = authenticate(graph)
    assert result["status"] == "needs_confirmation"

    fakes.confirmation = "cancel"
    result = send(graph, "no, don't")

    assert result["status"] == "cancelled"
    assert fakes.tools.calls == []


def test_unclear_confirmation_asks_again(graph, fakes):
    fakes.intent = "block_debit_card"
    send(graph, "Block my debit card")
    authenticate(graph)

    fakes.confirmation = "unclear"
    result = send(graph, "why should I?")

    assert result["status"] == "needs_confirmation"
    assert "yes" in result["response"].lower()
    assert fakes.tools.calls == []


def test_expired_session_reauthenticates_and_resumes_same_request(graph, fakes):
    fakes.intent = "issue_chequebook"
    send(graph, "Order a chequebook")
    authenticate(graph)
    send(graph, "50")
    send(graph, "home")

    config = {"configurable": {"thread_id": CONVERSATION_ID}}
    pending_key = graph.get_state(config).values["execution_key"]
    assert pending_key

    fakes.sessions.expire()
    result = send(graph, "yes")

    assert result["status"] == "needs_auth"
    assert "expired" in result["response"].lower()
    assert fakes.tools.calls == []  # the write did not run without a session

    result = authenticate(graph)

    assert result["status"] == "ok"
    assert "req-chequebook" in result["response"]
    assert len(fakes.tools.calls) == 1
    # Resumed with the exact same idempotency key (no duplicate request).
    assert fakes.tools.calls[0]["execution_key"] == pending_key


def test_real_tools_refuse_to_read_data_without_a_session(monkeypatch):
    from app.services import bank_tools, banking
    from app.services.bank_auth import SessionRequired
    from tests.conftest import FakeSessions

    accessed: list[str] = []
    monkeypatch.setattr(
        banking, "list_accounts", lambda user_id: accessed.append(user_id) or []
    )
    tools = bank_tools.BankTools(FakeSessions())

    with pytest.raises(SessionRequired):
        tools.get_account_balance(user_id=USER_ID, conversation_id=CONVERSATION_ID)
    assert accessed == []


# ---- pure validators ------------------------------------------------------
def test_leaf_count_validator():
    assert _parse_leaf_count("50") == 50
    assert _parse_leaf_count("100 leaves") == 100
    for invalid in ("5", "201", "", "many"):
        with pytest.raises(InputError):
            _parse_leaf_count(invalid)


def test_delivery_validator():
    assert _parse_delivery("home") == "home"
    assert _parse_delivery("At my home address") == "home"
    assert _parse_delivery("collect at the branch") == "branch"
    assert _parse_delivery("send by courier") == "courier"
    with pytest.raises(InputError):
        _parse_delivery("drone")
