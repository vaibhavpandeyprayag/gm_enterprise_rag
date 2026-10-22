"""Declarative registry of bank operations.

Everything operation-specific lives here - required fields, the question to ask,
the Python validator, the confirmation summary, the tool call and the response
formatting. The workflow itself only knows how to fill slots, validate, confirm
and execute, so new operations do not require new graph nodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from app.services.bank_actions import (
    CHEQUEBOOK_LEAF_MAX,
    CHEQUEBOOK_LEAF_MIN,
    DELIVERY_OPTIONS,
)


class InputError(ValueError):
    """A user-provided slot value failed validation."""


# --------------------------------------------------------------------------
# Input fields
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldSpec:
    name: str
    question: str
    description: str  # what the extractor should look for
    parse: Callable[[Any], Any]  # normalise + validate; raise InputError


def _parse_leaf_count(value: Any) -> int:
    digits = re.sub(r"[^\d]", "", str(value or ""))
    if not digits:
        raise InputError("Please give the number of cheque leaves as a whole number.")
    count = int(digits)
    if not CHEQUEBOOK_LEAF_MIN <= count <= CHEQUEBOOK_LEAF_MAX:
        raise InputError(
            f"The number of cheque leaves must be between "
            f"{CHEQUEBOOK_LEAF_MIN} and {CHEQUEBOOK_LEAF_MAX}."
        )
    return count


def _parse_delivery(value: Any) -> str:
    text = " ".join(str(value or "").lower().split())
    if any(word in text for word in ("home", "door", "residence", "address")):
        return "home"
    if any(word in text for word in ("branch", "bank")):
        return "branch"
    if any(word in text for word in ("courier", "post", "mail", "deliver")):
        return "courier"
    raise InputError(
        f"Please choose a delivery preference: {', '.join(DELIVERY_OPTIONS)}."
    )


LEAF_COUNT_FIELD = FieldSpec(
    name="leaf_count",
    question="How many cheque leaves would you like? (10-200)",
    description="the number of cheque leaves requested, as a whole number",
    parse=_parse_leaf_count,
)

DELIVERY_FIELD = FieldSpec(
    name="delivery_preference",
    question="How would you like it delivered - home, branch, or courier?",
    description="the delivery preference: home, branch, or courier",
    parse=_parse_delivery,
)


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    conversation_id: str
    execution_key: str
    values: Mapping[str, Any]


@dataclass(frozen=True)
class BankTask:
    key: str
    title: str
    description: str  # for the intent router
    fields: tuple[FieldSpec, ...]
    requires_confirmation: bool
    summarize: Callable[[Mapping[str, Any]], str]
    execute: Callable[[Any, ExecutionContext], dict[str, Any]]
    format_result: Callable[[dict[str, Any]], str]


def _execute_balance(tools: Any, ctx: ExecutionContext) -> dict[str, Any]:
    return tools.get_account_balance(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        execution_key=ctx.execution_key,
    )


def _execute_transactions(tools: Any, ctx: ExecutionContext) -> dict[str, Any]:
    return tools.get_last_transactions(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        execution_key=ctx.execution_key,
    )


def _execute_chequebook(tools: Any, ctx: ExecutionContext) -> dict[str, Any]:
    return tools.issue_chequebook(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        execution_key=ctx.execution_key,
        leaf_count=ctx.values["leaf_count"],
        delivery_preference=ctx.values["delivery_preference"],
    )


def _execute_block_card(tools: Any, ctx: ExecutionContext) -> dict[str, Any]:
    return tools.block_debit_card(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        execution_key=ctx.execution_key,
    )


def _fmt_money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def _fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        return value[:10]
    return ""


def _account_label(result: Mapping[str, Any]) -> str:
    account = result.get("account") or {}
    return f"{account.get('bank_name', 'your bank')} account ending {account.get('last4', '****')}"


def _format_balance(result: dict[str, Any]) -> str:
    currency = result.get("currency", "USD")
    return (
        f"Your {_account_label(result)} has a balance of "
        f"{_fmt_money(float(result.get('balance', 0.0)), currency)}."
    )


def _format_transactions(result: dict[str, Any]) -> str:
    account = result.get("account") or {}
    last4 = account.get("last4", "****")
    items = result.get("transactions") or []
    if not items:
        return f"No transactions were found for your account ending {last4}."

    currency = result.get("currency", "USD")
    lines = [
        f"Here are the last {len(items)} transactions for your account ending {last4}:",
        "",
        "| Date | Description | Amount | Status |",
        "| --- | --- | --- | --- |",
    ]
    for item in items:
        amount = float(item.get("amount", 0.0))
        description = str(item.get("description", "")).replace("|", "\\|")
        lines.append(
            f"| {_fmt_date(item.get('posted_at'))} | {description} | "
            f"{_fmt_money(amount, currency)} | {item.get('status', 'posted')} |"
        )
    return "\n".join(lines)


def _format_chequebook(result: dict[str, Any]) -> str:
    request = result.get("request") or {}
    if result.get("duplicate"):
        return (
            "That chequebook request was already processed. "
            f"Reference: {request.get('request_id')}."
        )
    return (
        "Your chequebook request has been created.\n\n"
        f"- Leaves: {request.get('leaf_count')}\n"
        f"- Delivery: {request.get('delivery_preference')}\n"
        f"- Reference: {request.get('request_id')}"
    )


def _format_block_card(result: dict[str, Any]) -> str:
    card = result.get("card") or {}
    last4 = card.get("last4") or "****"
    if result.get("already_blocked"):
        return f"The debit card ending {last4} is already blocked."
    return f"The debit card ending {last4} has been blocked."


TASKS: dict[str, BankTask] = {
    "get_account_balance": BankTask(
        key="get_account_balance",
        title="Account balance",
        description="Check the balance of the user's bank account.",
        fields=(),
        requires_confirmation=False,
        summarize=lambda values: "Check your account balance.",
        execute=_execute_balance,
        format_result=_format_balance,
    ),
    "get_last_transactions": BankTask(
        key="get_last_transactions",
        title="Recent transactions",
        description="Show the last five transactions on the user's bank account.",
        fields=(),
        requires_confirmation=False,
        summarize=lambda values: "Show your last five transactions.",
        execute=_execute_transactions,
        format_result=_format_transactions,
    ),
    "issue_chequebook": BankTask(
        key="issue_chequebook",
        title="Chequebook request",
        description=(
            "Order a new chequebook. Requires the number of cheque leaves "
            "(10-200) and a delivery preference (home, branch, or courier)."
        ),
        fields=(LEAF_COUNT_FIELD, DELIVERY_FIELD),
        requires_confirmation=True,
        summarize=lambda values: (
            "Please confirm this chequebook request:\n"
            f"- Leaves: {values.get('leaf_count')}\n"
            f"- Delivery: {values.get('delivery_preference')}"
        ),
        execute=_execute_chequebook,
        format_result=_format_chequebook,
    ),
    "block_debit_card": BankTask(
        key="block_debit_card",
        title="Block debit card",
        description="Block the debit card linked to the user's bank account.",
        fields=(),
        requires_confirmation=True,
        summarize=lambda values: "Please confirm: block the debit card linked to your account.",
        execute=_execute_block_card,
        format_result=_format_block_card,
    ),
}

DOCUMENT_QA = "document_qa"


def get_task(key: str) -> BankTask:
    try:
        return TASKS[key]
    except KeyError as exc:  # pragma: no cover - guarded by the intent schema
        raise InputError(f"Unknown bank operation '{key}'") from exc


def missing_fields(task: BankTask, values: Mapping[str, Any]) -> list[FieldSpec]:
    """Fields that still need a value, in registry order."""
    return [field for field in task.fields if field.name not in values]


def task_catalogue() -> str:
    """Human-readable operation list for the intent-router prompt."""
    return "\n".join(
        f"- {task.key}: {task.description}" for task in TASKS.values()
    )
