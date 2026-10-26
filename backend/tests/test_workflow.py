"""Workflow behaviour: routing, auth, slot filling, confirmation, idempotency."""

from __future__ import annotations

import pytest

from app.services.bank_auth import extract_email
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


def test_initial_bank_request_can_include_the_email(graph, fakes):
    fakes.intent = "get_account_balance"

    result = send(graph, f"Show my balance. My email is {EMAIL}")

    assert result["status"] == "needs_auth"
    assert result["auth_stage"] == "otp"
    assert EMAIL in result["response"]
    assert fakes.sessions.sessions == {}

    result = send(graph, OTP)
    assert result["status"] == "ok"
    assert [call["action"] for call in fakes.tools.calls] == ["balance"]


def test_initial_bank_request_with_unknown_email_stays_on_email_prompt(graph, fakes):
    fakes.intent = "get_account_balance"

    result = send(graph, "Show my balance. My email is stranger@example.com")

    assert result["status"] == "needs_auth"
    assert result["auth_stage"] == "email"
    assert "couldn't find" in result["response"].lower()
    assert fakes.sessions.sessions == {}


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
    lowered = result["response"].lower()
    assert "cheque leaves" in lowered
    assert "delivery" in lowered
    assert "home, branch, or courier" in lowered

    result = send(graph, "50")
    assert result["status"] == "needs_input"
    assert "delivered" in result["response"].lower()
    # The leaf count was accepted, so it is not asked again.
    assert "cheque leaves" not in result["response"].lower()

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


def test_chequebook_accepts_all_details_in_one_message(graph, fakes):
    fakes.intent = "issue_chequebook"
    send(graph, "Order a chequebook")
    assert authenticate(graph)["status"] == "needs_input"

    result = send(graph, "50 leaves, delivered to my home")

    assert result["status"] == "needs_confirmation"
    assert "Leaves: 50" in result["response"]
    assert "Delivery: home" in result["response"]
    assert fakes.tools.calls == []

    result = send(graph, "yes")
    assert result["status"] == "ok"
    call = fakes.tools.calls[0]
    assert call["leaf_count"] == 50
    assert call["delivery_preference"] == "home"


def test_chequebook_keeps_valid_details_when_another_is_invalid(graph, fakes):
    fakes.intent = "issue_chequebook"
    send(graph, "Order a chequebook")
    authenticate(graph)

    result = send(graph, "5 leaves, delivered to my home")

    assert result["status"] == "needs_input"
    assert "between 10 and 200" in result["response"]
    assert result["collected"]["delivery_preference"] == "home"
    # Only the invalid leaf count needs another answer.
    assert "delivered" not in result["response"].lower()


def test_cancelling_during_slot_filling_writes_nothing(graph, fakes):
    fakes.intent = "issue_chequebook"
    send(graph, "Order a chequebook")
    authenticate(graph)
    fakes.slot_action = "cancel"

    result = send(graph, "cancel")

    assert result["status"] == "cancelled"
    assert result["awaiting_field"] is None
    assert result["operation"] is None
    assert fakes.tools.calls == []


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


# ---- document questions can interrupt any bank prompt --------------------
def test_document_question_at_email_prompt_abandons_bank_request(graph, fakes):
    fakes.intent = "get_account_balance"
    assert send(graph, "Show my balance")["status"] == "needs_auth"

    fakes.intent = DOCUMENT_QA
    fakes.pending_reply = "new_request"
    result = send(graph, "What does my uploaded policy document say?")

    assert result["status"] == "ok"
    assert result["response"] == fakes.doc_answer
    assert result["operation"] is None
    assert result["pending_operation"] is None
    assert result["auth_stage"] is None
    assert fakes.sessions.sessions == {}
    assert fakes.tools.calls == []


def test_document_question_at_otp_prompt_abandons_bank_request(graph, fakes):
    fakes.intent = "get_account_balance"
    send(graph, "Show my balance")
    assert send(graph, EMAIL)["auth_stage"] == "otp"

    fakes.intent = DOCUMENT_QA
    fakes.pending_reply = "new_request"
    result = send(graph, "Summarise the employee handbook")

    assert result["status"] == "ok"
    assert result["response"] == fakes.doc_answer
    assert result["auth_stage"] is None
    assert fakes.sessions.sessions == {}  # no session was created
    assert fakes.tools.calls == []


def test_document_question_at_slot_prompt_abandons_bank_request(graph, fakes):
    fakes.intent = "issue_chequebook"
    send(graph, "Order a chequebook")
    assert authenticate(graph)["status"] == "needs_input"

    fakes.intent = DOCUMENT_QA
    fakes.pending_reply = "new_request"
    result = send(graph, "What does the PDF say about cheque books?")

    assert result["status"] == "ok"
    assert result["response"] == fakes.doc_answer
    assert result["awaiting_field"] is None
    assert result["collected"] == {}
    assert result["operation"] is None
    assert fakes.tools.calls == []


def test_document_question_at_confirmation_abandons_bank_request(graph, fakes):
    fakes.intent = "block_debit_card"
    send(graph, "Block my debit card")
    assert authenticate(graph)["status"] == "needs_confirmation"

    fakes.confirmation = "unclear"  # a question is not a yes/no
    fakes.intent = DOCUMENT_QA
    fakes.pending_reply = "new_request"
    result = send(graph, "What do my documents say about card blocking?")

    assert result["status"] == "ok"
    assert result["response"] == fakes.doc_answer
    assert result["confirmation_pending"] is False
    assert result["operation"] is None
    assert fakes.tools.calls == []


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


def test_email_extraction_ignores_surrounding_punctuation():
    assert extract_email("My email is Vp5@XYZ.com.") == "vp5@xyz.com"
    assert extract_email("<vp5@xyz.com>, thanks") == "vp5@xyz.com"
    assert extract_email("vp5@xyz.com?") == "vp5@xyz.com"
    assert extract_email("no email here") is None
