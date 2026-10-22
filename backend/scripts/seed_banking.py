"""Seed the banking collections with sample data for every user.

Creates one bank account per user and at least 20 transactions for that
account. The account / routing numbers are encrypted by the service layer,
so the script must go through `app.services.banking`.

Usage (from `backend/`):
    python -m scripts.seed_banking                 # every user
    python -m scripts.seed_banking --username bob  # only one user
    python -m scripts.seed_banking --min-transactions 20
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

from app.services import banking, mongo

# --------------------------------------------------------------------------
# Sample data
# --------------------------------------------------------------------------
BANKS = [
    ("HDFC Bank", "Everyday Checking", "checking", 12500.75, "021000021"),
    ("ICICI Bank", "Salary Savings", "savings", 84500.5, "026009800"),
    ("State Bank of India", "Savings Account", "savings", 31200.0, "400001234"),
    ("Axis Bank", "Cashback Credit Card", "credit_card", -4820.25, "610000001"),
    ("Kotak Mahindra Bank", "Investment Portfolio", "investment", 425000.0, "485000001"),
]

# (description, merchant, category, amount)
TRANSACTION_TEMPLATES = [
    ("Monthly rent", "Sunrise Estates", "Housing", -1850.00),
    ("Salary credit", "Acme Corporation", "Income", 62000.00),
    ("Groceries", "Fresh Basket", "Food", -1240.75),
    ("Electricity bill", "CityPower", "Utilities", -186.40),
    ("Internet bill", "FiberNet", "Utilities", -49.99),
    ("Fuel", "HP Petrol Pump", "Transport", -950.00),
    ("Coffee & breakfast", "Cafe Mocha", "Food", -18.50),
    ("Health insurance premium", "Star Health", "Insurance", -455.00),
    ("Mobile recharge", "Airtel", "Utilities", -799.00),
    ("Dining out", "Truffle Cafe", "Food", -64.20),
    ("Clothing", "Urban Threads", "Shopping", -2399.00),
    ("Medicine", "Apollo Pharmacy", "Health", -342.60),
    ("Ride share", "Uber", "Transport", -320.00),
    ("Book purchase", "Kindle Store", "Shopping", -599.00),
    ("Gym membership", "FitZone", "Health", -1200.00),
    ("Streaming subscription", "Netflix", "Entertainment", -649.00),
    ("Lunch", "Canteen 24", "Food", -210.00),
    ("Transfer to savings", "Own account", "Savings", -5000.00),
    ("Refund", "Amazon", "Income", 1299.00),
    ("Car service", "AutoCare", "Transport", -3800.00),
    ("Home loan EMI", "Mortgage Desk", "Housing", -24500.00),
    ("Dividend credit", "Zerodha", "Income", 3200.00),
    ("Water bill", "Municipal Corp", "Utilities", -78.00),
    ("Gift purchase", "Gift Shop", "Shopping", -1500.00),
]

CATEGORIES = ["Housing", "Income", "Food", "Utilities", "Transport", "Shopping", "Health"]
STATUSES = ["posted", "posted", "posted", "pending", "reversed"]


def seed_user(user: dict, min_transactions: int) -> tuple[int, int]:
    """Ensure the user has one account and at least `min_transactions` transactions."""
    user_id = user["user_id"]
    username = user.get("username", "?")

    accounts = banking.list_accounts(user_id)
    if accounts:
        account = accounts[0]
        created_account = 0
    else:
        bank, name, account_type, balance, routing = random.choice(BANKS)
        account = banking.create_account(
            user_id=user_id,
            bank_name=bank,
            account_name=name,
            account_number=generate_account_number(routing),
            account_type=account_type,
            routing_number=routing,
            currency="USD",
            balance=balance,
            is_primary=True,
            notes="Seeded account for the demo data set",
            opened_at=datetime.now(timezone.utc) - timedelta(days=400),
        )
        created_account = 1

    existing = mongo.transactions_collection().count_documents(
        {"user_id": user_id, "account_id": account["account_id"]}
    )
    missing = max(0, min_transactions - existing)

    # Spread the transactions over the last ~6 months, newest first.
    # The template index continues where the previous run stopped, so repeated
    # runs never create two transactions with the same description.
    now = datetime.now(timezone.utc)
    for offset in range(missing):
        index = existing + offset
        description, merchant, category, amount = TRANSACTION_TEMPLATES[
            index % len(TRANSACTION_TEMPLATES)
        ]
        posted_at = now - timedelta(
            days=(index * 7) + random.randint(0, 5),
            hours=random.randint(8, 20),
        )
        banking.create_transaction(
            user_id=user_id,
            account_id=account["account_id"],
            amount=amount,
            description=description,
            posted_at=posted_at,
            merchant=merchant,
            category=category,
            currency="USD",
            status=STATUSES[index % len(STATUSES)],
        )

    print(
        f"  {username}: account {'created' if created_account else 'kept'} "
        f"({account['bank_name']} / {account['account_number_masked']}), "
        f"transactions {existing} -> {existing + missing}"
    )
    return created_account, missing


def generate_account_number(routing: str) -> str:
    """A deterministic-looking 16 digit account number."""
    prefix = routing[-6:].replace("0", "4")
    return (prefix + "".join(str(random.randint(0, 9)) for _ in range(10)))[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed bank accounts and transactions.")
    parser.add_argument("--username", default=None, help="only seed this user")
    parser.add_argument("--min-transactions", type=int, default=24)
    args = parser.parse_args()

    if not mongo.ping():
        print("MongoDB unreachable - check MONGODB_URI")
        return 1

    query = {"username": args.username} if args.username else {}
    users = list(mongo.users_collection().find(query))
    if not users:
        print(f"No user found for {args.username!r}" if args.username else "No users found")
        return 1

    print(f"Seeding {len(users)} user(s) with >= {args.min_transactions} transactions each:")
    for user in users:
        seed_user(user, args.min_transactions)

    # Report
    db = mongo.get_db()
    print("\nResult:")
    for user in users:
        uid = user["user_id"]
        accounts = db["bank_accounts"].count_documents({"user_id": uid})
        transactions = db["transactions"].count_documents({"user_id": uid})
        print(f"  {user.get('username')}: {accounts} account(s), {transactions} transaction(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
