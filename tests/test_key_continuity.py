"""Server-side signing-key continuity for new versions (store.create_version).

Threat: a stolen API key lets an attacker publish a new version of the
victim's skill signed with the ATTACKER's key. The signature still verifies
(against the submitted key), so without a continuity check the only backstop
is a human moderator comparing hex strings. The server must reject a silent
key swap; only a moderator may rotate a lost key (the documented out-of-band
flow).

Run:  pytest tests/test_key_continuity.py   (no database needed -- fake pool)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import signing, store

SLUG = "key-continuity-test"
ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"


class FakeConn:
    """Minimal asyncpg stand-in: serves canned rows, records writes."""

    def __init__(self, *, skill, is_moderator, prior_keys):
        self.skill = skill
        self.is_moderator = is_moderator
        self.prior_keys = prior_keys
        self.inserts = 0

    # -- async context manager plumbing (acquire() / transaction())
    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    # -- query surface used by store.create_version
    async def fetchrow(self, sql, *args):
        if "from skills where slug" in sql:
            return self.skill
        if "insert into skill_versions" in sql:
            self.inserts += 1
            return {"id": "ver-2", "version": args[1]}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetchval(self, sql, *args):
        if "is_moderator" in sql:
            return self.is_moderator
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql, *args):
        if "distinct signer_pubkey" in sql:
            return [{"signer_pubkey": k} for k in self.prior_keys]
        raise AssertionError(f"unexpected fetch: {sql}")

    async def execute(self, sql, *args):
        return "EXECUTE 1"


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return self._conn


def make_payload(version, priv_hex, pub_hex):
    md = "# " + SLUG + "\n\n" + ("A test skill body for key continuity. " * 10)
    assert 50 < len(md) < 200_000
    sig = signing.sign_package(SLUG, version, md, priv_hex)
    return dict(skill_md=md, manifest={}, signature=sig, public_key=pub_hex)


def run_create_version(payload, *, is_moderator=False, prior_keys):
    skill = {"id": "skill-1", "author_account_id": ACCOUNT_ID}
    conn = FakeConn(skill=skill, is_moderator=is_moderator, prior_keys=prior_keys)
    coro = store.create_version(
        FakePool(conn), ACCOUNT_ID, SLUG, version="1.0.1", **payload)
    import asyncio
    return asyncio.run(coro), conn


def test_same_key_version_accepted():
    priv, pub = signing.generate_keypair()
    payload = make_payload("1.0.1", priv, pub)
    out, conn = run_create_version(payload, prior_keys=[pub])
    assert out["approved"] in (True, False)
    assert conn.inserts == 1


def test_changed_key_rejected_for_author():
    _, pub = signing.generate_keypair()
    # attacker holds the account's API key, signs with their OWN key
    attacker_priv, attacker_pub = signing.generate_keypair()
    payload = make_payload("1.0.1", attacker_priv, attacker_pub)
    skill = {"id": "skill-1", "author_account_id": ACCOUNT_ID}
    conn = FakeConn(skill=skill, is_moderator=False, prior_keys=[pub])
    import asyncio
    with pytest.raises(ValueError, match="signing key changed"):
        asyncio.run(store.create_version(
            FakePool(conn), ACCOUNT_ID, SLUG, version="1.0.1", **payload))
    # the check fires before the insert: nothing was written
    assert conn.inserts == 0


def test_changed_key_allowed_for_moderator():
    # Documented out-of-band rotation: a moderator rotates a lost key.
    priv, pub = signing.generate_keypair()
    new_priv, new_pub = signing.generate_keypair()
    payload = make_payload("1.0.1", new_priv, new_pub)
    out, conn = run_create_version(
        payload, is_moderator=True, prior_keys=[pub])
    assert conn.inserts == 1


def test_key_match_is_case_insensitive():
    priv, pub = signing.generate_keypair()
    payload = make_payload("1.0.1", priv, pub.upper())
    out, conn = run_create_version(payload, prior_keys=[pub.lower()])
    assert conn.inserts == 1


def test_either_known_key_accepted():
    # Skill rotated once before (via moderator): both keys are known.
    priv1, pub1 = signing.generate_keypair()
    priv2, pub2 = signing.generate_keypair()
    payload = make_payload("1.0.1", priv2, pub2)
    out, conn = run_create_version(payload, prior_keys=[pub1, pub2])
    assert conn.inserts == 1


def test_rejection_maps_to_400_not_404():
    # api/main.py maps ValueError -> 404 only when the message starts
    # with "no "; a key-change rejection must be a 400.
    priv, pub = signing.generate_keypair()
    attacker_priv, _ = signing.generate_keypair()
    _, attacker_pub = signing.generate_keypair()
    payload = make_payload("1.0.1", attacker_priv, attacker_pub)
    with pytest.raises(ValueError) as excinfo:
        run_create_version(payload, prior_keys=[pub])
    assert not str(excinfo.value).startswith("no ")
