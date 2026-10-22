"""Encryption for sensitive banking fields (bank account / routing numbers).

The account number is the one piece of data in this app that must never be
stored in the clear, so it is encrypted with **Fernet** (AES-128-CBC +
HMAC-SHA256, authenticated) before it reaches MongoDB and decrypted only when
it is rendered for the owner of the account.

Key handling
------------
`BANK_ENCRYPTION_KEYS` holds one or more comma-separated keys:

  * the **first** key is used to encrypt,
  * **every** key is tried when decrypting.

That is what makes a rotation safe: add the new key first, re-encrypt the
existing rows (or leave them - they keep decrypting with the old key), then
drop the old key.

A key may be either

  * a Fernet key (44-character urlsafe base64 of 32 bytes), or
  * an arbitrary passphrase, which is stretched into a Fernet key with
    PBKDF2-HMAC-SHA256 and the configured salt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

# Number of PBKDF2 iterations used to stretch a passphrase into a key.
_PBKDF2_ROUNDS = 600_000


class EncryptionError(Exception):
    """Raised when a value cannot be encrypted or decrypted."""


class DecryptionError(EncryptionError):
    """Raised when no configured key can decrypt the value."""


def _derive_key(material: str, salt: str) -> bytes:
    """Stretch an arbitrary passphrase into a 32-byte Fernet key."""
    derived = hashlib.pbkdf2_hmac(
        "sha256", material.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS
    )
    return base64.urlsafe_b64encode(derived)


def _as_fernet_key(material: str, salt: str) -> bytes:
    """Return a valid Fernet key for the given configuration entry."""
    candidate = material.strip()
    # A well-formed Fernet key is 44 urlsafe-base64 characters (32 bytes).
    if len(candidate) == 44:
        try:
            base64.urlsafe_b64decode(candidate.encode("utf-8"))
            return candidate.encode("utf-8")
        except Exception:
            pass
    return _derive_key(candidate, salt)


@lru_cache(maxsize=1)
def _fernets() -> tuple[Fernet, ...]:
    """Build the Fernet instances (primary first)."""
    settings = get_settings()
    if not settings.bank_encryption_keys:
        raise EncryptionError(
            "No encryption key configured. Set BANK_ENCRYPTION_KEYS in .env "
            "(generate one with: python -m scripts.gen_bank_key)."
        )
    salt = settings.bank_encryption_salt
    return tuple(
        Fernet(_as_fernet_key(material, salt))
        for material in settings.bank_encryption_keys
    )


def encrypt(plaintext: str) -> str:
    """Encrypt a string; the result is safe to store in MongoDB."""
    if plaintext is None:
        raise EncryptionError("Nothing to encrypt")
    primary = _fernets()[0]
    return primary.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt a value produced by `encrypt`.

    Tries every configured key, so data encrypted with an older key stays
    readable after a rotation.
    """
    if not token:
        raise DecryptionError("Nothing to decrypt")
    try:
        raw = token.encode("utf-8")
    except Exception as exc:
        raise DecryptionError("Malformed encrypted value") from exc

    for fernet in _fernets():
        try:
            return fernet.decrypt(raw).decode("utf-8")
        except InvalidToken:
            continue
        except Exception:
            continue
    raise DecryptionError(
        "Could not decrypt the value - the encryption key has changed or is wrong."
    )


def try_decrypt(token: str | None) -> str | None:
    """Decrypt, returning None instead of raising when it is not possible."""
    if not token:
        return None
    try:
        return decrypt(token)
    except EncryptionError:
        return None


def mask(value: str | None, keep: int = 4) -> str:
    """Mask a sensitive number for display, e.g. '****4321'."""
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    tail = digits[-keep:] if keep > 0 else ""
    return f"****{tail}" if tail else "****"


def last4(value: str | None) -> str:
    """Return the last four digits of a number (stored in the clear, indexed)."""
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[-4:]


def fingerprint(value: str | None) -> str:
    """A stable, non-reversible fingerprint used to detect duplicates."""
    if not value:
        return ""
    return hmac.new(
        key=hashlib.sha256(b"genrag.bank.fingerprint").digest(),
        msg=value.strip().encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()[:16]
