"""Banking domain service: bank accounts and transactions.

Both collections are strictly per user (`user_id`), and the sensitive account
number is encrypted at rest (see `app/services/encryption.py`).

The service owns the data rules (validation, defaults, encryption, cascade
delete); the API layer only translates HTTP <-> these functions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.services import encryption, mongo

# Account types the UI offers.
ACCOUNT_TYPES = (
    "checking",
    "savings",
    "credit_card",
    "investment",
    "loan",
    "other",
)

# Transaction statuses.
TRANSACTION_STATUSES = ("posted", "pending", "reversed")

# A single page of transactions.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class BankingError(Exception):
    """Raised for invalid banking data."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------
# Bank accounts
# --------------------------------------------------------------------------
def create_account(
    *,
    user_id: str,
    bank_name: str,
    account_name: str,
    account_number: str,
    account_type: str = "checking",
    routing_number: str | None = None,
    currency: str = "USD",
    balance: float = 0.0,
    is_primary: bool = False,
    notes: str | None = None,
    opened_at: datetime | None = None,
) -> dict[str, Any]:
    """Create a bank account; the account number is encrypted before storage."""
    bank_name = (bank_name or "").strip()
    account_name = (account_name or "").strip()
    account_number = (account_number or "").strip()

    if not bank_name:
        raise BankingError("Bank name is required")
    if not account_name:
        raise BankingError("Account name is required")
    if not account_number:
        raise BankingError("Account number is required")
    if account_type not in ACCOUNT_TYPES:
        raise BankingError(
            f"Unknown account type '{account_type}'. Use: {', '.join(ACCOUNT_TYPES)}"
        )

    fingerprint = encryption.fingerprint(account_number)
    collection = mongo.bank_accounts_collection()
    if collection.find_one(
        {"user_id": user_id, "account_number_fingerprint": fingerprint}
    ):
        raise BankingError("This account number is already saved", status_code=409)

    now = _utcnow()
    document = {
        "account_id": _new_id(),
        "user_id": user_id,
        "bank_name": bank_name,
        "account_name": account_name,
        "account_type": account_type,
        "account_number_encrypted": encryption.encrypt(account_number),
        "account_number_last4": encryption.last4(account_number),
        "account_number_fingerprint": fingerprint,
        "routing_number_encrypted": (
            encryption.encrypt(routing_number.strip()) if routing_number else None
        ),
        "currency": (currency or "USD").strip().upper()[:3],
        "balance": float(balance or 0.0),
        "is_primary": bool(is_primary),
        "notes": (notes or "").strip() or None,
        "opened_at": opened_at,
        "created_at": now,
        "updated_at": now,
    }

    # Only one primary account per user.
    if document["is_primary"]:
        collection.update_many(
            {"user_id": user_id, "is_primary": True},
            {"$set": {"is_primary": False}},
        )

    collection.insert_one({**document, "_id": document["account_id"]})
    return _account_out(document)


def list_accounts(user_id: str) -> list[dict[str, Any]]:
    """All bank accounts of the user, primary first then newest."""
    cursor = (
        mongo.bank_accounts_collection()
        .find({"user_id": user_id})
        .sort([("is_primary", -1), ("created_at", -1)])
    )
    return [_account_out(doc) for doc in cursor]


def get_account(account_id: str, user_id: str) -> dict[str, Any]:
    """One bank account (decrypted for display)."""
    doc = mongo.bank_accounts_collection().find_one(
        {"account_id": account_id, "user_id": user_id}
    )
    if doc is None:
        raise BankingError("Account not found", status_code=404)
    return _account_out(doc)


def update_account(
    account_id: str,
    user_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Patch a bank account; the account number can be replaced (re-encrypted)."""
    collection = mongo.bank_accounts_collection()
    doc = collection.find_one({"account_id": account_id, "user_id": user_id})
    if doc is None:
        raise BankingError("Account not found", status_code=404)

    update: dict[str, Any] = {}

    for field in ("bank_name", "account_name", "notes"):
        if field in changes and changes[field] is not None:
            value = str(changes[field]).strip()
            if field in ("bank_name", "account_name") and not value:
                raise BankingError(f"{field.replace('_', ' ').title()} is required")
            update[field] = value or None

    if "account_type" in changes and changes["account_type"] is not None:
        if changes["account_type"] not in ACCOUNT_TYPES:
            raise BankingError(
                f"Unknown account type '{changes['account_type']}'. "
                f"Use: {', '.join(ACCOUNT_TYPES)}"
            )
        update["account_type"] = changes["account_type"]

    if "currency" in changes and changes["currency"]:
        update["currency"] = str(changes["currency"]).strip().upper()[:3]

    if "balance" in changes and changes["balance"] is not None:
        update["balance"] = float(changes["balance"])

    if "opened_at" in changes:
        update["opened_at"] = changes["opened_at"]

    if "account_number" in changes and changes["account_number"]:
        number = str(changes["account_number"]).strip()
        fingerprint = encryption.fingerprint(number)
        if collection.find_one(
            {
                "user_id": user_id,
                "account_number_fingerprint": fingerprint,
                "account_id": {"$ne": account_id},
            }
        ):
            raise BankingError("This account number is already saved", status_code=409)
        update["account_number_encrypted"] = encryption.encrypt(number)
        update["account_number_last4"] = encryption.last4(number)
        update["account_number_fingerprint"] = fingerprint

    if "routing_number" in changes:
        routing = (changes["routing_number"] or "").strip()
        update["routing_number_encrypted"] = encryption.encrypt(routing) if routing else None

    if "is_primary" in changes and changes["is_primary"] is not None:
        update["is_primary"] = bool(changes["is_primary"])

    if not update:
        return _account_out(doc)

    update["updated_at"] = _utcnow()
    collection.update_one(
        {"account_id": account_id, "user_id": user_id}, {"$set": update}
    )

    # Only one primary account per user.
    if update.get("is_primary"):
        collection.update_many(
            {"user_id": user_id, "account_id": {"$ne": account_id}, "is_primary": True},
            {"$set": {"is_primary": False}},
        )

    refreshed = collection.find_one({"account_id": account_id, "user_id": user_id})
    return _account_out(refreshed)


def delete_account(account_id: str, user_id: str) -> dict[str, Any]:
    """Delete an account and every transaction that belongs to it."""
    collection = mongo.bank_accounts_collection()
    result = collection.delete_one({"account_id": account_id, "user_id": user_id})
    if result.deleted_count == 0:
        raise BankingError("Account not found", status_code=404)

    deleted_transactions = mongo.transactions_collection().delete_many(
        {"account_id": account_id, "user_id": user_id}
    ).deleted_count

    return {
        "account_id": account_id,
        "deleted_transactions": deleted_transactions,
    }


def _account_out(doc: dict[str, Any]) -> dict[str, Any]:
    """Shape a stored account for the API (decrypting the sensitive fields)."""
    account_number = encryption.try_decrypt(doc.get("account_number_encrypted"))
    routing_number = encryption.try_decrypt(doc.get("routing_number_encrypted"))
    return {
        "account_id": doc["account_id"],
        "bank_name": doc.get("bank_name", ""),
        "account_name": doc.get("account_name", ""),
        "account_type": doc.get("account_type", "checking"),
        "account_number": account_number or "",
        "account_number_masked": encryption.mask(account_number),
        "account_number_last4": doc.get("account_number_last4", ""),
        "routing_number": routing_number or "",
        "currency": doc.get("currency", "USD"),
        "balance": doc.get("balance", 0.0),
        "is_primary": bool(doc.get("is_primary", False)),
        "notes": doc.get("notes"),
        "opened_at": doc.get("opened_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------
def create_transaction(
    *,
    user_id: str,
    account_id: str,
    amount: float,
    description: str,
    posted_at: datetime | None = None,
    merchant: str | None = None,
    category: str | None = None,
    currency: str | None = None,
    status: str = "posted",
    balance_after: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record a transaction against one of the user's accounts.

    `amount` is signed: negative = money out (debit), positive = money in (credit).
    """
    _require_account(account_id, user_id)

    description = (description or "").strip()
    if not description:
        raise BankingError("Description is required")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise BankingError("Amount must be a number") from None
    if amount == 0:
        raise BankingError("Amount cannot be zero")

    if status not in TRANSACTION_STATUSES:
        raise BankingError(
            f"Unknown status '{status}'. Use: {', '.join(TRANSACTION_STATUSES)}"
        )

    now = _utcnow()
    document = {
        "transaction_id": _new_id(),
        "user_id": user_id,
        "account_id": account_id,
        "amount": amount,
        "currency": (currency or "USD").strip().upper()[:3],
        "description": description,
        "merchant": (merchant or "").strip() or None,
        "category": (category or "").strip() or None,
        "status": status,
        "posted_at": posted_at or now,
        "balance_after": balance_after,
        "notes": (notes or "").strip() or None,
        "created_at": now,
    }

    mongo.transactions_collection().insert_one(
        {**document, "_id": document["transaction_id"]}
    )
    return _transaction_out(document)


def list_transactions(
    user_id: str,
    *,
    account_id: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """A page of the user's transactions, newest first."""
    limit = max(1, min(int(limit or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))
    offset = max(0, int(offset or 0))

    query: dict[str, Any] = {"user_id": user_id}
    if account_id:
        query["account_id"] = account_id
    if start or end:
        posted: dict[str, Any] = {}
        if start:
            posted["$gte"] = start
        if end:
            posted["$lte"] = end
        query["posted_at"] = posted

    collection = mongo.transactions_collection()
    total = collection.count_documents(query)
    cursor = (
        collection.find(query)
        .sort("posted_at", -1)
        .skip(offset)
        .limit(limit)
    )
    return {
        "items": [_transaction_out(doc) for doc in cursor],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_transaction(transaction_id: str, user_id: str) -> dict[str, Any]:
    """One transaction."""
    doc = mongo.transactions_collection().find_one(
        {"transaction_id": transaction_id, "user_id": user_id}
    )
    if doc is None:
        raise BankingError("Transaction not found", status_code=404)
    return _transaction_out(doc)


def update_transaction(
    transaction_id: str,
    user_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Patch a transaction."""
    collection = mongo.transactions_collection()
    doc = collection.find_one(
        {"transaction_id": transaction_id, "user_id": user_id}
    )
    if doc is None:
        raise BankingError("Transaction not found", status_code=404)

    update: dict[str, Any] = {}

    if "description" in changes and changes["description"] is not None:
        description = str(changes["description"]).strip()
        if not description:
            raise BankingError("Description is required")
        update["description"] = description

    if "amount" in changes and changes["amount"] is not None:
        try:
            amount = float(changes["amount"])
        except (TypeError, ValueError):
            raise BankingError("Amount must be a number") from None
        if amount == 0:
            raise BankingError("Amount cannot be zero")
        update["amount"] = amount

    for field in ("merchant", "category", "notes"):
        if field in changes:
            value = (changes[field] or "").strip() if isinstance(changes[field], str) else None
            update[field] = value or None

    if "status" in changes and changes["status"] is not None:
        if changes["status"] not in TRANSACTION_STATUSES:
            raise BankingError(
                f"Unknown status '{changes['status']}'. "
                f"Use: {', '.join(TRANSACTION_STATUSES)}"
            )
        update["status"] = changes["status"]

    if "currency" in changes and changes["currency"]:
        update["currency"] = str(changes["currency"]).strip().upper()[:3]

    if "posted_at" in changes and changes["posted_at"] is not None:
        update["posted_at"] = changes["posted_at"]

    if "balance_after" in changes:
        update["balance_after"] = changes["balance_after"]

    if "account_id" in changes and changes["account_id"]:
        _require_account(changes["account_id"], user_id)
        update["account_id"] = changes["account_id"]

    if not update:
        return _transaction_out(doc)

    collection.update_one(
        {"transaction_id": transaction_id, "user_id": user_id}, {"$set": update}
    )
    refreshed = collection.find_one(
        {"transaction_id": transaction_id, "user_id": user_id}
    )
    return _transaction_out(refreshed)


def delete_transaction(transaction_id: str, user_id: str) -> dict[str, Any]:
    """Delete one transaction."""
    result = mongo.transactions_collection().delete_one(
        {"transaction_id": transaction_id, "user_id": user_id}
    )
    if result.deleted_count == 0:
        raise BankingError("Transaction not found", status_code=404)
    return {"transaction_id": transaction_id, "status": "deleted"}


def _require_account(account_id: str, user_id: str) -> dict[str, Any]:
    """Make sure the account exists and belongs to the user."""
    doc = mongo.bank_accounts_collection().find_one(
        {"account_id": account_id, "user_id": user_id}
    )
    if doc is None:
        raise BankingError("Account not found", status_code=404)
    return doc


def _transaction_out(doc: dict[str, Any]) -> dict[str, Any]:
    """Shape a stored transaction for the API."""
    amount = doc.get("amount", 0.0)
    return {
        "transaction_id": doc["transaction_id"],
        "account_id": doc.get("account_id"),
        "amount": amount,
        "direction": "credit" if amount > 0 else "debit",
        "currency": doc.get("currency", "USD"),
        "description": doc.get("description", ""),
        "merchant": doc.get("merchant"),
        "category": doc.get("category"),
        "status": doc.get("status", "posted"),
        "posted_at": doc.get("posted_at"),
        "balance_after": doc.get("balance_after"),
        "notes": doc.get("notes"),
        "created_at": doc.get("created_at"),
    }
