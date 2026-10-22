"""Banking endpoints: bank accounts and transactions.

Both resources are strictly per user - every query is filtered by `user_id`.
The bank account number is encrypted at rest and only decrypted for the owner.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services import banking
from app.services.banking import BankingError

router = APIRouter(prefix="/api", tags=["banking"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class AccountIn(BaseModel):
    bank_name: str = Field(min_length=1, max_length=120)
    account_name: str = Field(min_length=1, max_length=120)
    account_number: str = Field(min_length=1, max_length=64)
    account_type: str = banking.ACCOUNT_TYPES[0]
    routing_number: str | None = Field(default=None, max_length=32)
    currency: str = "USD"
    balance: float = 0.0
    is_primary: bool = False
    notes: str | None = Field(default=None, max_length=500)
    opened_at: datetime | None = None


class AccountPatch(BaseModel):
    bank_name: str | None = Field(default=None, max_length=120)
    account_name: str | None = Field(default=None, max_length=120)
    account_number: str | None = Field(default=None, max_length=64)
    account_type: str | None = None
    routing_number: str | None = Field(default=None, max_length=32)
    currency: str | None = None
    balance: float | None = None
    is_primary: bool | None = None
    notes: str | None = Field(default=None, max_length=500)
    opened_at: datetime | None = None


class TransactionIn(BaseModel):
    account_id: str = Field(min_length=1)
    amount: float
    description: str = Field(min_length=1, max_length=300)
    posted_at: datetime | None = None
    merchant: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=60)
    currency: str | None = None
    status: str = "posted"
    balance_after: float | None = None
    notes: str | None = Field(default=None, max_length=500)


class TransactionPatch(BaseModel):
    account_id: str | None = None
    amount: float | None = None
    description: str | None = Field(default=None, max_length=300)
    posted_at: datetime | None = None
    merchant: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=60)
    currency: str | None = None
    status: str | None = None
    balance_after: float | None = None
    notes: str | None = Field(default=None, max_length=500)


class TransactionPage(BaseModel):
    items: list[dict]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _fail(exc: BankingError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _changes(body: BaseModel) -> dict[str, Any]:
    """Only the fields that were actually sent (excludes unset ones)."""
    return body.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------
# Bank accounts
# --------------------------------------------------------------------------
@router.get("/accounts")
def list_accounts(user: dict = Depends(get_current_user)) -> list[dict]:
    """All bank accounts of the current user."""
    return banking.list_accounts(user["user_id"])


@router.post("/accounts", status_code=201)
def create_account(
    body: AccountIn,
    user: dict = Depends(get_current_user),
) -> dict:
    """Add a bank account (the number is encrypted before it is stored)."""
    try:
        return banking.create_account(user_id=user["user_id"], **body.model_dump())
    except BankingError as exc:
        raise _fail(exc) from exc


@router.get("/accounts/{account_id}")
def get_account(account_id: str, user: dict = Depends(get_current_user)) -> dict:
    """One bank account."""
    try:
        return banking.get_account(account_id, user["user_id"])
    except BankingError as exc:
        raise _fail(exc) from exc


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: str,
    body: AccountPatch,
    user: dict = Depends(get_current_user),
) -> dict:
    """Update a bank account."""
    try:
        return banking.update_account(account_id, user["user_id"], _changes(body))
    except BankingError as exc:
        raise _fail(exc) from exc


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a bank account together with its transactions."""
    try:
        return banking.delete_account(account_id, user["user_id"])
    except BankingError as exc:
        raise _fail(exc) from exc


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------
@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    user: dict = Depends(get_current_user),
    account_id: str | None = Query(default=None, description="Filter by account"),
    limit: int = Query(default=banking.DEFAULT_PAGE_SIZE, ge=1, le=banking.MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    start: datetime | None = Query(default=None, description="Posted on/after this date"),
    end: datetime | None = Query(default=None, description="Posted on/before this date"),
) -> TransactionPage:
    """A page of the current user's transactions, newest first."""
    page = banking.list_transactions(
        user["user_id"],
        account_id=account_id,
        limit=limit,
        offset=offset,
        start=start,
        end=end,
    )
    return TransactionPage(**page)


@router.post("/transactions", status_code=201)
def create_transaction(
    body: TransactionIn,
    user: dict = Depends(get_current_user),
) -> dict:
    """Record a transaction. Negative amounts are debits, positive are credits."""
    try:
        return banking.create_transaction(
            user_id=user["user_id"], **body.model_dump()
        )
    except BankingError as exc:
        raise _fail(exc) from exc


@router.get("/transactions/{transaction_id}")
def get_transaction(
    transaction_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """One transaction."""
    try:
        return banking.get_transaction(transaction_id, user["user_id"])
    except BankingError as exc:
        raise _fail(exc) from exc


@router.patch("/transactions/{transaction_id}")
def update_transaction(
    transaction_id: str,
    body: TransactionPatch,
    user: dict = Depends(get_current_user),
) -> dict:
    """Update a transaction."""
    try:
        return banking.update_transaction(
            transaction_id, user["user_id"], _changes(body)
        )
    except BankingError as exc:
        raise _fail(exc) from exc


@router.delete("/transactions/{transaction_id}")
def delete_transaction(
    transaction_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Delete a transaction."""
    try:
        return banking.delete_transaction(transaction_id, user["user_id"])
    except BankingError as exc:
        raise _fail(exc) from exc


# --------------------------------------------------------------------------
# Reference data (for the UI dropdowns)
# --------------------------------------------------------------------------
@router.get("/banking/meta")
def banking_meta() -> dict[str, list[str]]:
    """The account types and transaction statuses the UI offers."""
    return {
        "account_types": list(banking.ACCOUNT_TYPES),
        "transaction_statuses": list(banking.TRANSACTION_STATUSES),
    }
