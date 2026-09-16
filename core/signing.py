"""Ed25519 signing for skill packages.

Threat model: a skill is executable instructions an agent will follow, so the
registry must prove *who* published *which exact bytes*. Every published
version carries:

    signature = sign(slug + "\\n" + version + "\\n" + skill_md)

made with the publisher's private key. The public key is stored alongside the
version. Clients verify on install; the server re-verifies on publish (which
also proves the submitter holds the private key).

Private keys NEVER leave the publisher's machine. The server only ever sees
public keys and signatures.
"""
from __future__ import annotations

import base64
import binascii

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

SIGNATURE_ALGORITHM = "ed25519"


def canonical_bytes(slug: str, version: str, skill_md: str) -> bytes:
    """The exact bytes that get signed. Both sides must build these identically."""
    return f"{slug}\n{version}\n{skill_md}".encode("utf-8")


def generate_keypair() -> tuple[str, str]:
    """Generate a fresh ed25519 keypair.

    Returns (private_key_hex, public_key_hex). The private key must be stored
    securely by the publisher and never sent to the server.
    """
    signing_key = SigningKey.generate()
    return signing_key.encode().hex(), signing_key.verify_key.encode().hex()


def sign_package(slug: str, version: str, skill_md: str, private_key_hex: str) -> str:
    """Sign a skill package. Returns the base64-encoded signature."""
    try:
        signing_key = SigningKey(bytes.fromhex(private_key_hex.strip()))
    except (ValueError, binascii.Error) as exc:
        raise ValueError("private key is not valid hex") from exc
    signed = signing_key.sign(canonical_bytes(slug, version, skill_md))
    return base64.b64encode(signed.signature).decode("ascii")


def verify_package(
    slug: str,
    version: str,
    skill_md: str,
    signature_b64: str,
    public_key_hex: str,
) -> bool:
    """Verify a skill package signature. Returns True only if it checks out."""
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex.strip()))
        signature = base64.b64decode(signature_b64.strip())
        verify_key.verify(canonical_bytes(slug, version, skill_md), signature)
        return True
    except (BadSignatureError, ValueError, binascii.Error):
        return False
