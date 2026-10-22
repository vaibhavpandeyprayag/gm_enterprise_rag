"""Write operations behind the bank workflow: chequebooks and debit cards.

These functions own the database rules (validation, masking, idempotency) and
are only called by the authorized tools in `services/bank_tools.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from app.services import mongo
from app.services.banking import BankingError

# Chequebook request rules (shared with the task registry for input validation).
CHEQUEBOOK_LEAF_MIN = 10
CHEQUEBOOK_LEAF_MAX = 200
DELIVERY_OPTIONS = ("home", "branch", "courier")

DEBIT_CARD_STATUSES = ("active", "blocked")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------
# Chequebook requests
# --------------------------------------------------------------------------
def create_chequebook_request(
    *,
    user_id: str,
    account_id: str,
    leaf_count: int,
    delivery_preference: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Create a chequebook request, returning the existing one on a retry.

    `(user_id, idempotency_key)` is unique, so a resumed graph or a retried
    request can never create a second order.
    """
    leaf_count = int(leaf_count)
    if not CHEQUEBOOK_LEAF_MIN <= leaf_count <= CHEQUEBOOK_LEAF_MAX:
        raise BankingError(
            f"Cheque-leaf count must be between {CHEQUEBOOK_LEAF_MIN} and "
            f"{CHEQUEBOOK_LEAF_MAX}"
        )
    delivery = (delivery_preference or "").strip().lower()
    if delivery not in DELIVERY_OPTIONS:
        raise BankingError(
            f"Delivery preference must be one of: {', '.join(DELIVERY_OPTIONS)}"
        )
    if not idempotency_key:
        raise BankingError("Missing idempotency key")

    collection = mongo.chequebook_requests_collection()
    query = {"user_id": user_id, "idempotency_key": idempotency_key}
    existing = collection.find_one(query)
    if existing is not None:
        return {"request": _request_out(existing), "duplicate": True}

    now = _utcnow()
    document = {
        "request_id": _new_id(),
        "user_id": user_id,
        "account_id": account_id,
        "leaf_count": leaf_count,
        "delivery_preference": delivery,
        "status": "requested",
        "idempotency_key": idempotency_key,
        "created_at": now,
    }
    try:
        collection.insert_one({**document, "_id": document["request_id"]})
    except DuplicateKeyError:
        # Another worker/resume won the race - return the stored request.
        stored = collection.find_one(query)
        if stored is None:
            raise
        return {"request": _request_out(stored), "duplicate": True}

    return {"request": _request_out(document), "duplicate": False}


def _request_out(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": doc["request_id"],
        "account_id": doc.get("account_id"),
        "leaf_count": doc.get("leaf_count"),
        "delivery_preference": doc.get("delivery_preference"),
        "status": doc.get("status", "requested"),
    }


# --------------------------------------------------------------------------
# Debit cards
# --------------------------------------------------------------------------
def ensure_debit_card(
    *,
    user_id: str,
    account_id: str,
    card_number_last4: str,
) -> dict[str, Any]:
    """Create the (demo) debit card for an account if it does not exist yet.

    Used by `scripts/seed_banking.py`. Banking data is never invented on the
    fly during a conversation - only the seed script provisions cards.
    """
    collection = mongo.debit_cards_collection()
    existing = collection.find_one({"account_id": account_id})
    if existing is not None:
        return _card_out(existing)

    now = _utcnow()
    document = {
        "card_id": _new_id(),
        "user_id": user_id,
        "account_id": account_id,
        "card_number_last4": (card_number_last4 or "")[-4:],
        "status": "active",
        "blocked_at": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        collection.insert_one({**document, "_id": document["card_id"]})
    except DuplicateKeyError:
        existing = collection.find_one({"account_id": account_id})
        if existing is None:
            raise
        return _card_out(existing)
    return _card_out(document)


def get_debit_card(user_id: str, account_id: str) -> dict[str, Any] | None:
    """The debit card linked to one of the user's accounts (None when absent)."""
    doc = mongo.debit_cards_collection().find_one(
        {"user_id": user_id, "account_id": account_id}
    )
    return _card_out(doc) if doc else None


def block_debit_card(user_id: str, account_id: str) -> dict[str, Any]:
    """Block the debit card; blocking twice is a safe no-op."""
    collection = mongo.debit_cards_collection()
    doc = collection.find_one({"user_id": user_id, "account_id": account_id})
    if doc is None:
        raise BankingError(
            "No debit card is linked to this account, so it cannot be blocked.",
            status_code=404,
        )

    was_blocked = doc.get("status") == "blocked"
    if not was_blocked:
        now = _utcnow()
        collection.update_one(
            {"card_id": doc["card_id"], "user_id": user_id},
            {"$set": {"status": "blocked", "blocked_at": now, "updated_at": now}},
        )
        doc = collection.find_one({"card_id": doc["card_id"], "user_id": user_id})

    return {"card": _card_out(doc), "already_blocked": was_blocked}


def _card_out(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": doc["card_id"],
        "account_id": doc.get("account_id"),
        "last4": doc.get("card_number_last4", ""),
        "status": doc.get("status", "active"),
        "blocked_at": doc.get("blocked_at"),
    }
