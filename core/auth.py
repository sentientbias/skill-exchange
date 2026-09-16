"""API-key authentication.

Keys look like ``skx_<32 url-safe chars>``. Only a SHA-256 hash is stored in
the database; the plaintext is shown to the account owner exactly once, at
creation time. There is no recovery path -- if a key is lost, revoke it and
mint a new one.
"""
from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "skx_"


def new_api_key() -> tuple[str, str, str]:
    """Mint a fresh API key.

    Returns (full_key, key_hash, key_prefix). Persist key_hash + key_prefix,
    show full_key to the user ONCE, then forget it.
    """
    full_key = KEY_PREFIX + secrets.token_urlsafe(32)
    return full_key, hash_key(full_key), full_key[:12]


def hash_key(full_key: str) -> str:
    """SHA-256 hex digest of an API key (what we store)."""
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def extract_bearer(authorization: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
