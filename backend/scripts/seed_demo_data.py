"""Reset and seed two demo users with bank accounts, transactions and cards.

Users (the bank-auth step matches these emails exactly):
    vp5   / vp5@xyz.com     (the configured implicit owner)
    demo  / demo@xyz.com

The OTP is not stored per user: it is the shared development code in
`BANK_OTP_CODE` (123456 by default).

`refresh_demo_data()` is called on every application startup. It deletes the
demo users' bank accounts, transactions, debit cards, chequebook requests and
bank sessions and recreates the exact sample state, so the database always
starts from the same demo data (cards active, balances/transactions reset,
no leftover orders). Other users' banking data is left alone.

All banking data goes through the service layer so account numbers are
encrypted and cards are created with the same rules as production.

Usage (from `backend/`):
    python -m scripts.seed_demo_data
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import bank_actions, banking, mongo

# --------------------------------------------------------------------------
# Demo data
# --------------------------------------------------------------------------
DEMO_USERS: list[dict[str, Any]] = [
    {
        "username": "vp5",
        "email": "vp5@xyz.com",
        "account": {
            "bank_name": "HDFC Bank",
            "account_name": "Everyday Checking",
            "account_number": "5010023456789012",
            "account_type": "checking",
            "routing_number": "021000021",
            "balance": 128430.75,
            "notes": "Primary demo account",
        },
        "transactions": [
            (2, "Salary credit", "Acme Corporation", "Income", 85000.00, "posted"),
            (4, "Monthly rent", "Sunrise Estates", "Housing", -2200.00, "posted"),
            (6, "Groceries", "Fresh Basket", "Food", -3450.75, "posted"),
            (8, "Electricity bill", "CityPower", "Utilities", -2860.40, "posted"),
            (10, "Coffee & breakfast", "Cafe Mocha", "Food", -450.00, "posted"),
            (13, "Fuel", "HP Petrol Pump", "Transport", -3200.00, "posted"),
            (17, "Refund", "Amazon", "Income", 1299.00, "posted"),
            (21, "Streaming subscription", "Netflix", "Entertainment", -649.00, "posted"),
            (26, "Health insurance premium", "Star Health", "Insurance", -4550.00, "posted"),
            (31, "Transfer to savings", "Own account", "Savings", -10000.00, "posted"),
            (38, "Dining out", "Truffle Cafe", "Food", -1850.20, "posted"),
            (45, "Mobile recharge", "Airtel", "Utilities", -799.00, "pending"),
        ],
        "chequebook": {
            "idempotency_key": "seed-vp5-chequebook",
            "leaf_count": 50,
            "delivery_preference": "home",
        },
    },
    {
        "username": "demo",
        "email": "demo@xyz.com",
        "account": {
            "bank_name": "ICICI Bank",
            "account_name": "Salary Savings",
            "account_number": "0260098012345678",
            "account_type": "savings",
            "routing_number": "026009800",
            "balance": 84250.50,
            "notes": "Secondary demo account",
        },
        "transactions": [
            (3, "Salary credit", "Globex Ltd", "Income", 62000.00, "posted"),
            (5, "Home loan EMI", "Mortgage Desk", "Housing", -24500.00, "posted"),
            (7, "Groceries", "Daily Mart", "Food", -2180.60, "posted"),
            (9, "Internet bill", "FiberNet", "Utilities", -999.00, "posted"),
            (12, "Ride share", "Uber", "Transport", -320.00, "posted"),
            (16, "Dividend credit", "Zerodha", "Income", 3200.00, "posted"),
            (23, "Pharmacy", "Apollo Pharmacy", "Health", -342.60, "posted"),
            (29, "Gym membership", "FitZone", "Health", -1200.00, "posted"),
            (36, "Car service", "AutoCare", "Transport", -3800.00, "posted"),
            (44, "Gift purchase", "Gift Shop", "Shopping", -1500.00, "posted"),
        ],
        "chequebook": {
            "idempotency_key": "seed-demo-chequebook",
            "leaf_count": 25,
            "delivery_preference": "branch",
        },
    },
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Seeding steps
# --------------------------------------------------------------------------
def ensure_user(username: str, email: str) -> dict[str, Any]:
    """Create the user, or backfill the email on an existing username."""
    users = mongo.users_collection()
    existing = users.find_one({"username": username})
    if existing is not None:
        if (existing.get("email") or "").strip().lower() != email:
            users.update_one(
                {"user_id": existing["user_id"]}, {"$set": {"email": email}}
            )
            existing["email"] = email
        return existing

    user = {
        "user_id": str(uuid.uuid4()),
        "username": username,
        "email": email,
        "created_at": _utcnow(),
    }
    users.insert_one({**user, "_id": user["user_id"]})
    return user


def ensure_account(user: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Reuse the user's account, or create the demo one."""
    accounts = banking.list_accounts(user["user_id"])
    if accounts:
        return accounts[0]

    return banking.create_account(
        user_id=user["user_id"],
        bank_name=spec["bank_name"],
        account_name=spec["account_name"],
        account_number=spec["account_number"],
        account_type=spec["account_type"],
        routing_number=spec["routing_number"],
        currency="USD",
        balance=spec["balance"],
        is_primary=True,
        notes=spec["notes"],
        opened_at=_utcnow() - timedelta(days=420),
    )


def seed_transactions(
    user: dict[str, Any], account: dict[str, Any], rows: list[tuple]
) -> int:
    """Add the demo transactions once (nothing is added if data already exists)."""
    collection = mongo.transactions_collection()
    existing = collection.count_documents(
        {"user_id": user["user_id"], "account_id": account["account_id"]}
    )
    if existing:
        return 0

    now = _utcnow()
    for days_ago, description, merchant, category, amount, status in rows:
        banking.create_transaction(
            user_id=user["user_id"],
            account_id=account["account_id"],
            amount=amount,
            description=description,
            posted_at=now - timedelta(days=days_ago, hours=3),
            merchant=merchant,
            category=category,
            currency="USD",
            status=status,
        )
    return len(rows)


def seed_chequebook(
    user: dict[str, Any], account: dict[str, Any], spec: dict[str, Any]
) -> bool:
    """Create one demo chequebook request (the fixed key keeps reruns safe)."""
    result = bank_actions.create_chequebook_request(
        user_id=user["user_id"],
        account_id=account["account_id"],
        leaf_count=spec["leaf_count"],
        delivery_preference=spec["delivery_preference"],
        idempotency_key=spec["idempotency_key"],
    )
    return not result.get("duplicate")


def _reset_banking_data(user_ids: list[str]) -> None:
    """Delete every banking record owned by the demo users (sessions included)."""
    if not user_ids:
        return
    scope = {"user_id": {"$in": user_ids}}
    mongo.transactions_collection().delete_many(scope)
    mongo.debit_cards_collection().delete_many(scope)
    mongo.chequebook_requests_collection().delete_many(scope)
    mongo.bank_accounts_collection().delete_many(scope)
    # Old authorizations must not survive the reset.
    mongo.bank_sessions_collection().delete_many(scope)


def refresh_demo_data() -> None:
    """Reset the demo banking data and recreate the sample state.

    Called on every application startup, so cards become active again, balances
    and transactions are restored, and test orders/sessions disappear. Only the
    two demo users are touched.
    """
    users = [ensure_user(item["username"], item["email"]) for item in DEMO_USERS]
    _reset_banking_data([user["user_id"] for user in users])

    for item, user in zip(DEMO_USERS, users):
        account = ensure_account(user, item["account"])
        added = seed_transactions(user, account, item["transactions"])
        card = bank_actions.ensure_debit_card(
            user_id=user["user_id"],
            account_id=account["account_id"],
            card_number_last4=account.get("account_number_last4", ""),
        )
        created_request = seed_chequebook(user, account, item["chequebook"])
        print(
            f"[seed] {item['username']} <{item['email']}>: "
            f"{account['bank_name']} {account['account_number_masked']} "
            f"(balance {account['currency']} {account['balance']:,.2f}), "
            f"card {card['last4']} ({card['status']}), "
            f"transactions {added}, "
            f"chequebook {'created' if created_request else 'kept'}"
        )


def _print_summary() -> None:
    print("\nSample bank data:")
    for item in DEMO_USERS:
        user = mongo.users_collection().find_one({"username": item["username"]})
        if user is None:  # pragma: no cover - created by refresh_demo_data
            continue
        uid = user["user_id"]
        print(
            f"  {item['username']}: "
            f"{mongo.bank_accounts_collection().count_documents({'user_id': uid})} account(s), "
            f"{mongo.transactions_collection().count_documents({'user_id': uid})} transaction(s), "
            f"{mongo.debit_cards_collection().count_documents({'user_id': uid})} card(s), "
            f"{mongo.chequebook_requests_collection().count_documents({'user_id': uid})} chequebook request(s)"
        )
    print("Bank OTP for both users: 123456 (BANK_OTP_CODE).")


def main() -> int:
    if not mongo.ping():
        print("MongoDB unreachable - check MONGODB_URI")
        return 1

    mongo.ensure_indexes()
    refresh_demo_data()
    _print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
