"""LangGraph workflow: intent -> auth -> slot filling -> confirm -> tool.

Conditional edges pick the next step from the shared state, so a single graph
handles document Q&A, bank reads, two write operations, multi-turn input
collection, conversation-scoped authentication and resuming a pending request.

Security properties:
  * document Q&A never touches the bank session store;
  * the LLM only classifies/extracts - Python validates every value;
  * authorization comes from the server-side session store, checked again by
    each bank tool immediately before it reads or writes data.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.services.bank_auth import SessionRequired, extract_email
from app.services.bank_tasks import (
    DOCUMENT_QA,
    TASKS,
    ExecutionContext,
    InputError,
    missing_fields,
)
from app.services.banking import BankingError
from app.workflow.deps import WorkflowDeps
from app.workflow.state import WorkflowState

_OTP_PATTERN = re.compile(r"\b(\d{6})\b")
_CANCEL_WORDS = {
    "cancel",
    "cancel it",
    "cancel that",
    "stop",
    "abort",
    "never mind",
    "nevermind",
    "forget it",
}

AUTH_EMAIL_PROMPT = (
    "Bank operations need a quick identity check first.\n\n"
    "What is the email registered with your bank? You can also ask a question "
    "about your documents at any time."
)
AUTH_EMAIL_RETRY = (
    "I couldn't find an account with that email. Please check the address and "
    "send the email registered with your bank."
)
AUTH_OTP_PROMPT = "Thanks. Enter the 6-digit verification code sent to {email}."
SESSION_EXPIRED_PROMPT = (
    "Your secure bank session has expired, so nothing was submitted. To continue "
    "the pending request, please enter your registered email again."
)


def _is_cancel(text: str) -> bool:
    return " ".join((text or "").lower().split()).strip(" .!?") in _CANCEL_WORDS


def _cancelled(message: str) -> dict[str, Any]:
    """Clear the whole bank flow (used by cancel paths)."""
    return {
        "response": message,
        "status": "cancelled",
        "intent": None,
        "operation": None,
        "pending_operation": None,
        "pending_action": None,
        "auth_stage": None,
        "auth_email": None,
        "auth_user_id": None,
        "verified_user_id": None,
        "otp_attempts": 0,
        "resume_to": None,
        "collected": {},
        "awaiting_field": None,
        "confirmation_pending": False,
        "confirmed": False,
        "execution_key": None,
        "sources": [],
        "rephrased_query": "",
    }


def _abandoned_state() -> dict[str, Any]:
    """Forget a pending bank flow so the current message can be routed fresh.

    The server-side bank session (if one exists) is intentionally kept: only the
    unfinished *request* is reset. Document Q&A never needs the session anyway.
    """
    return {
        "abandoned": True,
        "intent": None,
        "operation": None,
        "pending_operation": None,
        "pending_action": None,
        "auth_stage": None,
        "auth_email": None,
        "auth_user_id": None,
        "verified_user_id": None,
        "otp_attempts": 0,
        "resume_to": None,
        "collected": {},
        "awaiting_field": None,
        "confirmation_pending": False,
        "confirmed": False,
        "execution_key": None,
        "sources": [],
        "rephrased_query": "",
        "status": "ok",
    }


def _cleared_operation() -> dict[str, Any]:
    """Post-execution state: no pending request, no temporary authorization."""
    return {
        "intent": None,
        "operation": None,
        "pending_operation": None,
        "pending_action": None,
        "auth_stage": None,
        "auth_email": None,
        "auth_user_id": None,
        "verified_user_id": None,
        "otp_attempts": 0,
        "resume_to": None,
        "collected": {},
        "awaiting_field": None,
        "confirmation_pending": False,
        "confirmed": False,
        "execution_key": None,
        "sources": [],
        "rephrased_query": "",
    }


def build_graph(deps: WorkflowDeps, checkpointer: Any = None) -> Any:
    """Build and compile the workflow graph."""

    # ---- entry: resume whatever step the conversation is waiting on ------
    def entry(state: WorkflowState) -> str:
        if state.get("auth_stage") in {"email", "otp"}:
            return "auth_email" if state["auth_stage"] == "email" else "auth_otp"
        if state.get("awaiting_field"):
            return "collect_input"
        if state.get("confirmation_pending"):
            return "confirm_input"
        return "classify"

    def is_new_request(pending: str, state: WorkflowState) -> bool:
        """True when the message changes topic instead of answering `pending`.

        Used by every paused bank step, so a document question can interrupt an
        email / OTP / slot / confirmation prompt. The caller then clears the
        pending request and sends the message back through intent routing.
        """
        return (
            deps.classify_pending_reply(
                pending,
                state.get("question", ""),
                state.get("history", []),
                deps.settings,
            )
            == "new_request"
        )

    # ---- intent routing --------------------------------------------------
    def classify(state: WorkflowState) -> dict[str, Any]:
        operation = deps.classify_intent(
            state.get("question", ""), state.get("history", []), deps.settings
        )
        if operation == DOCUMENT_QA:
            return {
                "intent": DOCUMENT_QA,
                "operation": None,
                "pending_operation": None,
                "pending_action": None,
                "abandoned": False,
                "status": "ok",
                "sources": [],
                "rephrased_query": "",
            }
        # A fresh bank request starts with an empty slot bag.
        return {
            "intent": operation,
            "operation": operation,
            "collected": {},
            "awaiting_field": None,
            "confirmation_pending": False,
            "confirmed": False,
            "execution_key": None,
            "pending_operation": None,
            "pending_action": None,
            "sources": [],
            "rephrased_query": "",
            "status": "ok",
            "abandoned": False,
        }

    def after_classify(state: WorkflowState) -> str:
        return "document_qa" if state.get("intent") == DOCUMENT_QA else "authorize"

    # ---- document QA (never requires bank authentication) ---------------
    def document_qa(state: WorkflowState) -> dict[str, Any]:
        result = deps.answer_question(
            state.get("question", ""),
            state.get("history", []),
            state.get("user_id", ""),
        )
        return {
            "response": result.answer,
            "sources": result.sources,
            "rephrased_query": getattr(result, "rephrased_query", "") or "",
            "status": "ok",
            "operation": None,
            "pending_operation": None,
            "pending_action": None,
        }

    # ---- bank authorization ---------------------------------------------
    def authorize(state: WorkflowState) -> dict[str, Any]:
        operation = state.get("operation") or ""
        session = deps.sessions.get(state.get("conversation_id", ""))
        if session is None:
            return {
                "pending_operation": operation,
                "pending_action": "start",
                "auth_stage": "email",
                "verified_user_id": None,
                "response": AUTH_EMAIL_PROMPT,
                "status": "needs_auth",
            }
        return {
            "verified_user_id": session.get("user_id"),
            "auth_stage": None,
            "pending_operation": None,
            "pending_action": None,
            "status": "ok",
        }

    def after_authorize(state: WorkflowState) -> str:
        return END if state.get("auth_stage") else "slots"

    def auth_email(state: WorkflowState) -> dict[str, Any]:
        question = state.get("question", "")
        if _is_cancel(question):
            return _cancelled("Okay, I've cancelled that bank request. Nothing was changed.")

        email = extract_email(question)
        if not email:
            # No email in the message: it may be a document question instead.
            if is_new_request("the email address registered on the account", state):
                return _abandoned_state()
            return {
                "response": AUTH_EMAIL_PROMPT,
                "status": "needs_auth",
                "auth_stage": "email",
            }

        user = deps.find_user_by_email(email)
        if user is None:
            return {
                "response": AUTH_EMAIL_RETRY,
                "status": "needs_auth",
                "auth_stage": "email",
            }

        return {
            "auth_email": email,
            "auth_user_id": user["user_id"],
            "auth_stage": "otp",
            "otp_attempts": 0,
            "response": AUTH_OTP_PROMPT.format(email=email),
            "status": "needs_auth",
        }

    def auth_otp(state: WorkflowState) -> dict[str, Any]:
        question = state.get("question", "")
        if _is_cancel(question):
            return _cancelled("Okay, I've cancelled that bank request. Nothing was changed.")

        match = _OTP_PATTERN.search(question)
        if not match:
            # No code in the message: it may be a document question instead.
            if is_new_request("the 6-digit verification code", state):
                return _abandoned_state()
            return {
                "response": (
                    "I didn't see a 6-digit code in that message. Please send "
                    "the 6-digit verification code we're waiting for."
                ),
                "status": "needs_auth",
                "auth_stage": "otp",
            }

        # The OTP is compared in Python against server configuration - the model
        # neither produces nor validates it.
        if match.group(1) != deps.otp_code:
            attempts = int(state.get("otp_attempts", 0)) + 1
            return {
                "response": (
                    "That code is incorrect. Please check the 6-digit code and "
                    "try again."
                ),
                "status": "needs_auth",
                "auth_stage": "otp",
                "otp_attempts": attempts,
            }

        verified_user_id = state.get("auth_user_id")
        if not verified_user_id:
            return {
                "response": (
                    "I lost track of the account we were verifying. Please "
                    "enter your registered email again."
                ),
                "status": "needs_auth",
                "auth_stage": "email",
            }

        deps.sessions.create(
            verified_user_id,
            state.get("conversation_id", ""),
            ttl_minutes=deps.ttl_minutes,
        )

        pending_action = state.get("pending_action")
        resume_to = (
            "execute"
            if pending_action == "execute" and state.get("operation")
            else "slots"
        )
        update: dict[str, Any] = {
            "verified_user_id": verified_user_id,
            "auth_stage": None,
            "auth_email": None,
            "auth_user_id": None,
            "otp_attempts": 0,
            "pending_action": None,
            "pending_operation": None,
            "resume_to": resume_to,
            "status": "ok",
        }
        if resume_to == "execute":
            # Preserve operation / collected inputs / confirmed / execution_key.
            pass
        else:
            update.update(
                {
                    "operation": state.get("pending_operation")
                    or state.get("operation"),
                    "collected": state.get("collected") or {},
                }
            )
        return update

    def after_auth_email(state: WorkflowState) -> str:
        return "classify" if state.get("abandoned") else END

    def after_auth_otp(state: WorkflowState) -> str:
        if state.get("abandoned"):
            return "classify"
        # A rejected/missing code keeps auth_stage set: stop and ask again.
        if state.get("auth_stage") in {"email", "otp"}:
            return END
        return "execute" if state.get("resume_to") == "execute" else "slots"

    # ---- reusable slot filling ------------------------------------------
    def slots(state: WorkflowState) -> dict[str, Any]:
        task = TASKS.get(state.get("operation") or "")
        if task is None:
            return {
                "response": "I'm not sure which bank task you meant. I can check "
                "your account balance, show your recent transactions, request a "
                "chequebook, or block your debit card. Which one would you like?",
                "status": "error",
                **_cleared_operation(),
            }

        values = state.get("collected") or {}
        missing = missing_fields(task, values)
        if missing:
            field = missing[0]
            return {
                "awaiting_field": field.name,
                "response": field.question,
                "status": "needs_input",
            }

        if task.requires_confirmation:
            execution_key = state.get("execution_key") or str(uuid.uuid4())
            return {
                "execution_key": execution_key,
                "confirmation_pending": True,
                "confirmed": False,
                "awaiting_field": None,
                "response": task.summarize(values)
                + "\n\nReply **yes** to confirm or **no** to cancel.",
                "status": "needs_confirmation",
            }

        return {
            "awaiting_field": None,
            "confirmation_pending": False,
            "confirmed": False,
            "status": "ok",
        }

    def after_slots(state: WorkflowState) -> str:
        if state.get("awaiting_field") or state.get("confirmation_pending"):
            return END
        return "execute"

    def collect_input(state: WorkflowState) -> dict[str, Any]:
        task = TASKS.get(state.get("operation") or "")
        field_name = state.get("awaiting_field") or ""
        field = next(
            (spec for spec in (task.fields if task else ()) if spec.name == field_name),
            None,
        )
        if field is None:
            return {
                "response": (
                    "I'm sorry - I lost track of that request. Nothing was "
                    "changed. Please start again by telling me what you'd like "
                    "to do."
                ),
                "status": "error",
                **_cleared_operation(),
            }

        # A document question (or any new request) abandons the pending slot
        # instead of being forced into the field. Checked *before* extraction so
        # an unrelated message can never accidentally validate as a value.
        if is_new_request(field.question, state):
            return _abandoned_state()

        extraction = deps.extract_field(
            field,
            state.get("question", ""),
            state.get("history", []),
            deps.settings,
        )
        if extraction.action == "cancel":
            return _cancelled("Okay, I've cancelled that request. Nothing was changed.")

        try:
            value = field.parse(extraction.value)
        except InputError as exc:
            return {
                "response": f"{exc} {field.question}",
                "status": "needs_input",
                "awaiting_field": field.name,
            }

        collected = {**(state.get("collected") or {}), field.name: value}
        return {
            "collected": collected,
            "awaiting_field": None,
            "status": "ok",
        }

    # ---- confirmation ----------------------------------------------------
    def after_collect(state: WorkflowState) -> str:
        if state.get("abandoned"):
            return "classify"
        # Invalid input: keep the (error + question) reply and wait for the user.
        return END if state.get("awaiting_field") else "slots"

    def confirm_input(state: WorkflowState) -> dict[str, Any]:
        decision = deps.classify_confirmation(
            state.get("question", ""),
            state.get("history", []),
            deps.settings,
        )
        if decision == "confirm":
            return {
                "confirmed": True,
                "confirmation_pending": False,
                "status": "ok",
            }
        if decision == "cancel":
            return _cancelled("Okay, I've cancelled that request. Nothing was changed.")

        # The reply was not a clear yes/no - it may be a document question.
        if is_new_request(
            "Reply yes to confirm or no to cancel the pending bank request.", state
        ):
            return _abandoned_state()
        return {
            "response": (
                "I just need a clear yes or no: reply **yes** to confirm or "
                "**no** to cancel."
            ),
            "status": "needs_confirmation",
            "confirmation_pending": True,
            "confirmed": False,
        }

    def after_confirm(state: WorkflowState) -> str:
        if state.get("abandoned"):
            return "classify"
        return "execute" if state.get("confirmed") else END

    # ---- tool execution --------------------------------------------------
    def execute(state: WorkflowState) -> dict[str, Any]:
        task = TASKS.get(state.get("operation") or "")
        actor = state.get("verified_user_id") or ""
        if task is None or not actor:
            return {
                "response": (
                    "I'm sorry - I couldn't finish that request because some "
                    "details were missing. Nothing was changed. Please start "
                    "again."
                ),
                "status": "error",
                **_cleared_operation(),
            }

        context = ExecutionContext(
            user_id=actor,
            conversation_id=state.get("conversation_id", ""),
            execution_key=state.get("execution_key") or str(uuid.uuid4()),
            values=state.get("collected") or {},
        )
        try:
            result = task.execute(deps.tools, context)
        except SessionRequired:
            # Keep the pending operation, inputs, confirmation and idempotency
            # key so the OTP step can resume the exact same request.
            return {
                "pending_operation": task.key,
                "pending_action": "execute",
                "auth_stage": "email",
                "verified_user_id": None,
                "confirmation_pending": False,
                "response": SESSION_EXPIRED_PROMPT,
                "status": "needs_auth",
            }
        except BankingError as exc:
            return {
                "response": exc.message,
                "status": "error",
                **_cleared_operation(),
            }
        except Exception as exc:  # pragma: no cover - provider/database failure
            print(f"[workflow] {task.key} failed: {exc}")
            return {
                "response": "I'm sorry - I ran into a problem while completing "
                "that request. Please try again in a moment.",
                "status": "error",
                **_cleared_operation(),
            }

        return {
            "response": task.format_result(result),
            "status": "ok",
            **_cleared_operation(),
        }

    # ---- graph wiring ----------------------------------------------------
    builder = StateGraph(WorkflowState)
    builder.add_node("classify", classify)
    builder.add_node("document_qa", document_qa)
    builder.add_node("authorize", authorize)
    builder.add_node("auth_email", auth_email)
    builder.add_node("auth_otp", auth_otp)
    builder.add_node("slots", slots)
    builder.add_node("collect_input", collect_input)
    builder.add_node("confirm_input", confirm_input)
    builder.add_node("execute", execute)

    builder.add_conditional_edges(
        START,
        entry,
        {
            "classify": "classify",
            "auth_email": "auth_email",
            "auth_otp": "auth_otp",
            "collect_input": "collect_input",
            "confirm_input": "confirm_input",
        },
    )
    builder.add_conditional_edges(
        "classify",
        after_classify,
        {"document_qa": "document_qa", "authorize": "authorize"},
    )
    builder.add_edge("document_qa", END)
    builder.add_conditional_edges(
        "authorize",
        after_authorize,
        {"slots": "slots", END: END},
    )
    # Normally ask for the OTP and stop: the code arrives in the next user turn,
    # where `entry` resumes at `auth_otp`. If the user changed topic instead,
    # `after_auth_email` sends the message back through `classify`.
    builder.add_conditional_edges(
        "auth_email",
        after_auth_email,
        {"classify": "classify", END: END},
    )
    builder.add_conditional_edges(
        "auth_otp",
        after_auth_otp,
        {"classify": "classify", "execute": "execute", "slots": "slots", END: END},
    )
    builder.add_conditional_edges(
        "slots",
        after_slots,
        {"execute": "execute", END: END},
    )
    builder.add_conditional_edges(
        "collect_input",
        after_collect,
        {"classify": "classify", "slots": "slots", END: END},
    )
    builder.add_conditional_edges(
        "confirm_input",
        after_confirm,
        {"classify": "classify", "execute": "execute", END: END},
    )
    builder.add_edge("execute", END)

    return builder.compile(checkpointer=checkpointer)
