"""Self-diagnosing signature failures on publish (store.create_skill /
store.create_version).

A flat "signature verification failed -- check key and content" is a dead
end for autonomous publishers: the common faults are invisible byte drift
(trailing whitespace, CRLF, paste mangling -- the 2026-09-16 seed corruption
that invalidated 5 signatures) or signing with the wrong keypair. The new
error must carry the server's canonical-bytes sha256 so the publisher can
recompute locally and learn whether their canonicalization matches the
server's or their signing step is at fault.

Run:  pytest tests/test_signature_diagnostics.py   (no database needed)
"""
import asyncio
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import signing, store

SLUG = "diag-test-skill"
ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"


def make_md():
    return "# diag test\n\n" + ("A test skill body for signature diagnostics. " * 10)


def expected_digest(slug, version, md):
    return hashlib.sha256(
        signing.canonical_bytes(slug, version, md)).hexdigest()


class FakeSkillConn:
    """Fake pool/conn for create_skill: canned inserts, records writes."""

    def __init__(self):
        self.inserts = 0

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchrow(self, sql, *args):
        if "insert into skills" in sql:
            self.inserts += 1
            return {"id": "skill-1", "slug": args[0]}
        if "insert into skill_versions" in sql:
            self.inserts += 1
            return {"id": "ver-1", "version": args[1]}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql, *args):
        return "EXECUTE 1"


class FakeVersionConn(FakeSkillConn):
    """Extends the skill fake with the create_version query surface."""

    def __init__(self, *, is_moderator=False, prior_keys=()):
        super().__init__()
        self.is_moderator = is_moderator
        self.prior_keys = list(prior_keys)
        self.skill = {"id": "skill-1", "author_account_id": ACCOUNT_ID}

    async def fetchrow(self, sql, *args):
        if "from skills where slug" in sql:
            return self.skill
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        if "is_moderator" in sql:
            return self.is_moderator
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql, *args):
        if "distinct signer_pubkey" in sql:
            return [{"signer_pubkey": k} for k in self.prior_keys]
        raise AssertionError(f"unexpected fetch: {sql}")


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


def skill_payload(priv_hex, pub_hex, md=None, version="1.0.0"):
    md = md if md is not None else make_md()
    sig = signing.sign_package(SLUG, version, md, priv_hex)
    return dict(
        name="Diag Test", slug=SLUG, description="diagnostic test skill",
        category="devtools", version=version, skill_md=md,
        manifest={}, signature=sig, public_key=pub_hex,
    )


def version_payload(priv_hex, pub_hex, md=None, version="1.0.1"):
    md = md if md is not None else make_md()
    sig = signing.sign_package(SLUG, version, md, priv_hex)
    return dict(
        version=version, skill_md=md, manifest={},
        signature=sig, public_key=pub_hex,
    )


def test_create_skill_tampered_bytes_reports_matching_digest():
    # Signed bytes X, submitted X + trailing space: the server's canonical
    # sha256 must equal the digest over the SUBMITTED bytes, proving to the
    # publisher that the server built different bytes than they signed.
    priv, pub = signing.generate_keypair()
    md = make_md()
    payload = skill_payload(priv, pub, md=md)
    payload["skill_md"] = md + " "  # invisible drift, signature now fails
    conn = FakeSkillConn()
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(store.create_skill(
            FakePool(conn), ACCOUNT_ID, **payload))
    msg = str(excinfo.value)
    assert "signature verification failed" in msg
    assert f"canonical_sha256={expected_digest(SLUG, '1.0.0', md + ' ')}" in msg
    assert "canonical_format" in msg
    assert "skill_md of" in msg  # received length, helps spot drift
    assert conn.inserts == 0  # nothing written on a failed publish


def test_create_skill_wrong_key_reports_digest_too():
    # Signed with key A, submitted key B: signature fails, and the digest
    # over the submitted bytes matches the publisher's own recomputation,
    # which is exactly the signal that says "your bytes are right, fix the
    # key" (hashes match) instead of "your bytes drifted".
    priv_a, _ = signing.generate_keypair()
    _, pub_b = signing.generate_keypair()
    md = make_md()
    payload = skill_payload(priv_a, pub_b, md=md)
    conn = FakeSkillConn()
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(store.create_skill(
            FakePool(conn), ACCOUNT_ID, **payload))
    msg = str(excinfo.value)
    assert "signature verification failed" in msg
    assert f"canonical_sha256={expected_digest(SLUG, '1.0.0', md)}" in msg
    assert conn.inserts == 0


def test_create_version_bad_signature_reports_digest():
    priv, pub = signing.generate_keypair()
    payload = version_payload(priv, pub)
    payload["signature"] = payload["signature"][:-4] + "AAAA"  # corrupt
    conn = FakeVersionConn(prior_keys=[pub])
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(store.create_version(
            FakePool(conn), ACCOUNT_ID, SLUG, **payload))
    msg = str(excinfo.value)
    assert "signature verification failed" in msg
    assert f"canonical_sha256={expected_digest(SLUG, '1.0.1', make_md())}" in msg
    assert conn.inserts == 0


def test_failure_maps_to_400_not_404():
    # api/main.py maps ValueError -> 404 only when the message starts with
    # "no "; a signature diagnostic must stay a 400.
    priv, pub = signing.generate_keypair()
    md = make_md()
    payload = skill_payload(priv, pub, md=md)
    payload["skill_md"] = md + " "  # signed X, submitted X + drift
    with pytest.raises(ValueError) as excinfo:
        asyncio.run(store.create_skill(
            FakePool(FakeSkillConn()), ACCOUNT_ID, **payload))
    assert not str(excinfo.value).startswith("no ")


def test_successful_publish_has_no_diagnostic():
    # A good signature still flows through untouched: no diagnostic text in
    # any success response, inserts happen.
    priv, pub = signing.generate_keypair()
    payload = skill_payload(priv, pub)
    conn = FakeSkillConn()
    out = asyncio.run(store.create_skill(
        FakePool(conn), ACCOUNT_ID, **payload))
    assert out["slug"] == SLUG
    assert "canonical_sha256" not in str(out)
    assert conn.inserts == 2
