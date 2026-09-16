"""Exchange Pro passes: ed25519-signed bearer tokens.

A pro pass grants its holder free access to the paid Exchange Pro endpoints
(the x402 seller). It bypasses payment, so unforgeability is the whole game:

Token format:  ``sp1.<b64url(payload)>.<b64url(signature)>``

* payload is compact JSON: {"v":1,"pid":<uuid>,"handle":<publisher handle>,
  "iat":<unix epoch>,"exp":<unix epoch>}  (exp = iat + 90 days)
* signature = ed25519(signing_key, ascii_bytes(b64url(payload)))

The seller verifies with the PUBLIC key only (see PROPASS_VERIFY_KEY in the
x402 seller). The private key lives in the PROPASS_SIGNING_KEY env var and
never leaves the Exchange API host.

Passes are bound to a handle inside the signed payload (non-transferable by
design for v1: sharing the token shares the identity). Expiry is enforced by
the seller; revocation is "don't re-issue" (v1 has no revocation list --
passes are short-lived at 90 days).
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
import time

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

TOKEN_PREFIX = "sp1"
PASS_TTL_SECONDS = 90 * 24 * 3600  # 90 days


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def mint_pass(handle: str, signing_key_hex: str) -> tuple[str, str, int]:
    """Mint a pro pass for ``handle``.

    Returns (token, pass_id, expires_at_epoch). Raises ValueError on bad key.
    """
    handle = (handle or "").strip().lower()
    if not handle:
        raise ValueError("handle is required")
    try:
        signing_key = SigningKey(bytes.fromhex(signing_key_hex.strip()))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("pro-pass signing key is not valid hex") from exc
    now = int(time.time())
    payload = {
        "v": 1,
        "pid": secrets.token_hex(16),
        "handle": handle,
        "iat": now,
        "exp": now + PASS_TTL_SECONDS,
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = signing_key.sign(payload_b64.encode("ascii")).signature
    token = f"{TOKEN_PREFIX}.{payload_b64}.{_b64e(sig)}"
    return token, payload["pid"], payload["exp"]


def verify_pass(token: str, verify_key_hex: str) -> dict | None:
    """Verify a pro-pass token. Returns the payload dict, or None if the
    token is malformed, has a bad signature, or is expired."""
    try:
        prefix, payload_b64, sig_b64 = (token or "").strip().split(".")
        if prefix != TOKEN_PREFIX:
            return None
        verify_key = VerifyKey(bytes.fromhex(verify_key_hex.strip()))
        verify_key.verify(payload_b64.encode("ascii"), _b64d(sig_b64))
        payload = json.loads(_b64d(payload_b64))
    except (BadSignatureError, ValueError, binascii.Error, KeyError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != 1:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= int(time.time()):
        return None
    if not payload.get("handle") or not payload.get("pid"):
        return None
    return payload
