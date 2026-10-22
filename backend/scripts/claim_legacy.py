"""Claim pre-auth data for a user.

Documents ingested before the multi-user feature have no `user_id`, so they are
invisible to every user (retrieval filters on `user_id`). This script assigns
those orphaned documents to an existing account so the old knowledge base keeps
working.

Usage (from the `backend/` directory):

    python -m scripts.claim_legacy --username alice
    python -m scripts.claim_legacy --username alice --dry-run

Add `backend/` to `PYTHONPATH` (or run from `backend/`) so `app` is importable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/claim_legacy.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import mongo  # noqa: E402


def claim(username: str, dry_run: bool = False) -> int:
    """Assign every orphan document (no user_id) to `username`'s user_id."""
    user = mongo.users_collection().find_one({"username": username.strip().lower()})
    if not user:
        print(
            f"User '{username}' does not exist - start the app once so the owner "
            "account is created (or set OWNER_USERNAME in .env)."
        )
        return 1

    user_id = user["user_id"]
    orphan = {"user_id": {"$exists": False}}

    for collection_name in ("files", "chunks", "conversations"):
        collection = mongo.get_db()[collection_name]
        count = collection.count_documents(orphan)
        print(f"{collection_name}: {count} orphaned document(s)")
        if dry_run or count == 0:
            continue
        result = collection.update_many(orphan, {"$set": {"user_id": user_id}})
        print(f"  -> claimed {result.modified_count} document(s) for user_id={user_id}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="Username that owns the data")
    parser.add_argument(
        "--dry-run", action="store_true", help="Only report what would change"
    )
    args = parser.parse_args()
    return claim(args.username, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
