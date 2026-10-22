"""Authorized banking tools used by the workflow's execute step.

Every public method starts by validating the conversation's server-side bank
session. The LangGraph state is never trusted for authorization, and the model
never receives a database handle - it can only trigger these fixed functions.
"""

from __future__ import annotations

from typing import Any

from app.services import bank_actions, banking
from app.services.bank_auth import BankSessionStore


def _account_ref(account: dict[str, Any]) -> dict[str, Any]:
    """Non-sensitive account identity used in responses and summaries."""
    return {
        "account_id": account["account_id"],
        "bank_name": account.get("bank_name", ""),
        "account_name": account.get("account_name", ""),
        "last4": account.get("account_number_last4", ""),
        "currency": account.get("currency", "USD"),
    }


class BankTools:
    """Fixed, validated banking operations bound to a session store."""

    def __init__(self, sessions: BankSessionStore | None = None) -> None:
        self.sessions = sessions or BankSessionStore()

    # ---- authorization --------------------------------------------------
    def _authorize(self, conversation_id: str, user_id: str) -> dict[str, Any]:
        """Raise SessionRequired unless a live session matches the user."""
        return self.sessions.require(conversation_id, user_id=user_id)

    def _account(self, user_id: str) -> dict[str, Any]:
        """The user's account (the product assumes exactly one)."""
        accounts = banking.list_accounts(user_id)
        if not accounts:
            raise banking.BankingError(
                "No bank account is linked to this profile.", status_code=404
            )
        return accounts[0]

    # ---- operations -----------------------------------------------------
    def get_account_balance(
        self,
        *,
        user_id: str,
        conversation_id: str,
        execution_key: str = "",
    ) -> dict[str, Any]:
        self._authorize(conversation_id, user_id)
        account = self._account(user_id)
        return {
            "account": _account_ref(account),
            "balance": float(account.get("balance", 0.0)),
            "currency": account.get("currency", "USD"),
        }

    def get_last_transactions(
        self,
        *,
        user_id: str,
        conversation_id: str,
        execution_key: str = "",
    ) -> dict[str, Any]:
        self._authorize(conversation_id, user_id)
        account = self._account(user_id)
        page = banking.list_transactions(
            user_id, account_id=account["account_id"], limit=5
        )
        return {
            "account": _account_ref(account),
            "transactions": page.get("items", []),
            "total": page.get("total", 0),
            "currency": account.get("currency", "USD"),
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
        self._authorize(conversation_id, user_id)
        account = self._account(user_id)
        result = bank_actions.create_chequebook_request(
            user_id=user_id,
            account_id=account["account_id"],
            leaf_count=leaf_count,
            delivery_preference=delivery_preference,
            idempotency_key=execution_key,
        )
        return {"account": _account_ref(account), **result}

    def block_debit_card(
        self,
        *,
        user_id: str,
        conversation_id: str,
        execution_key: str = "",
    ) -> dict[str, Any]:
        self._authorize(conversation_id, user_id)
        account = self._account(user_id)
        result = bank_actions.block_debit_card(user_id, account["account_id"])
        return {"account": _account_ref(account), **result}
