"""Minimal tests for the ed25519 signing flow (core/signing.py).

Run:  pytest tests/test_signing.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import signing

SLUG = "web-research"
VERSION = "1.0.0"
BODY = "# Web Research\n\nDo web research like an analyst."


def test_sign_and_verify_roundtrip():
    private_hex, public_hex = signing.generate_keypair()
    sig = signing.sign_package(SLUG, VERSION, BODY, private_hex)
    assert signing.verify_package(SLUG, VERSION, BODY, sig, public_hex)


def test_tampered_content_fails():
    private_hex, public_hex = signing.generate_keypair()
    sig = signing.sign_package(SLUG, VERSION, BODY, private_hex)
    assert not signing.verify_package(
        SLUG, VERSION, BODY + " (malicious addition)", sig, public_hex)


def test_wrong_slug_or_version_fails():
    private_hex, public_hex = signing.generate_keypair()
    sig = signing.sign_package(SLUG, VERSION, BODY, private_hex)
    assert not signing.verify_package("other-slug", VERSION, BODY, sig, public_hex)
    assert not signing.verify_package(SLUG, "9.9.9", BODY, sig, public_hex)


def test_wrong_public_key_fails():
    private_hex, _ = signing.generate_keypair()
    _, other_public_hex = signing.generate_keypair()
    sig = signing.sign_package(SLUG, VERSION, BODY, private_hex)
    assert not signing.verify_package(SLUG, VERSION, BODY, sig, other_public_hex)


def test_garbage_inputs_fail_cleanly():
    private_hex, public_hex = signing.generate_keypair()
    sig = signing.sign_package(SLUG, VERSION, BODY, private_hex)
    assert not signing.verify_package(SLUG, VERSION, BODY, "not-base64!!!",
                                      public_hex)
    assert not signing.verify_package(SLUG, VERSION, BODY, sig, "zzzz")
    assert not signing.verify_package(SLUG, VERSION, BODY, "", public_hex)
