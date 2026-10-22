"""Generate an encryption key for the banking collections.

Usage (from the `backend/` directory):

    python -m scripts.gen_bank_key

Prints a ready-to-paste Fernet key. Put it in the project-root `.env`:

    BANK_ENCRYPTION_KEYS=<the printed key>

To rotate a key later, list the new key FIRST and keep the old one after it:

    BANK_ENCRYPTION_KEYS=<new key>,<old key>

The first key encrypts, every key can decrypt - so existing rows keep working
until you re-encrypt them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="How many keys to generate (use for rotation)",
    )
    args = parser.parse_args()

    keys = [Fernet.generate_key().decode("utf-8") for _ in range(max(1, args.count))]
    print()
    for key in keys:
        print(f"BANK_ENCRYPTION_KEYS entry: {key}")
    print()
    print("Add to the project-root .env:")
    print(f"BANK_ENCRYPTION_KEYS={','.join(keys)}")
    print()
    print("Keep this file safe - without the key the account numbers are unreadable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
