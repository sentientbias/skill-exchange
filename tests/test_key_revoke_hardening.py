"""Key-revocation hardening: per-account budget + malformed key_id handling.

Two gaps closed on DELETE /api/v1/accounts/me/keys/{key_id}:

1. It was the only authenticated write route with no per-account rate
   budget. A stolen API key (threat 6) could revoke every other key on the
   account in seconds (account lockout / scorched earth) while the
   victim's recovery path -- minting replacement keys -- is throttled at
   5/60 s. Now DELETE shares the same 5/60 s budget shape as key minting.
2. key_id went raw into ``store.revoke_api_key`` which casts to uuid;
   garbage input raised inside asyncpg and surfaced as a 500. The route
   now validates the UUID at the boundary and answers 404 "no such key".

Run:  pytest tests/test_key_revoke_hardening.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import api.rate_limit as rl
from api import deps
from api.rate_limit import _reset_account, check_account_budget
from api.routers.accounts import revoke_key
from fastapi import HTTPException, Request


def _run(coro):
    return asyncio.run(coro)


def _request(method, path):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("203.0.113.7", 1234),
    }
    return Request(scope)


def _patch_clock(start=1_000_000.0):
    now = [start]
    real = rl._monotonic
    rl._monotonic = lambda: now[0]
    return now, real


def _unpatch_clock(real):
    rl._monotonic = real


def _drain(account, method, path, n):
    for _ in range(n):
        retry = check_account_budget(account, method, path)
        if retry > 0:
            return retry
    return 0.0


# ---------------------------------------------------------------------------
# per-account DELETE budget
# ---------------------------------------------------------------------------

def test_revoke_budget_allows_five_then_limits():
    _reset_account()
    now, real = _patch_clock()
    try:
        path = "/api/v1/accounts/me/keys/12345678-1234-5678-1234-567812345678"
        assert _drain("acct-a", "DELETE", path, 5) == 0.0
        assert check_account_budget("acct-a", "DELETE", path) > 0
    finally:
        _unpatch_clock(real)


def test_revoke_window_reopens():
    _reset_account()
    now, real = _patch_clock()
    try:
        path = "/api/v1/accounts/me/keys/12345678-1234-5678-1234-567812345678"
        assert _drain("acct-a", "DELETE", path, 5) == 0.0
        assert check_account_budget("acct-a", "DELETE", path) > 0
        now[0] += 61.0
        assert check_account_budget("acct-a", "DELETE", path) == 0.0
    finally:
        _unpatch_clock(real)


def test_revoke_budget_is_per_account():
    _reset_account()
    now, real = _patch_clock()
    try:
        path = "/api/v1/accounts/me/keys/12345678-1234-5678-1234-567812345678"
        assert _drain("acct-a", "DELETE", path, 5) == 0.0
        assert check_account_budget("acct-a", "DELETE", path) > 0
        assert check_account_budget("acct-b", "DELETE", path) == 0.0
    finally:
        _unpatch_clock(real)


def test_revoke_budget_isolated_from_mint_budget():
    """DELETE and POST on /accounts/me/keys are separate method buckets."""
    _reset_account()
    now, real = _patch_clock()
    try:
        key_id = "12345678-1234-5678-1234-567812345678"
        # exhaust the mint (POST) budget; revoke (DELETE) untouched
        assert _drain("acct-a", "POST", "/api/v1/accounts/me/keys", 5) == 0.0
        assert check_account_budget("acct-a", "POST", "/api/v1/accounts/me/keys") > 0
        assert check_account_budget(
            "acct-a", "DELETE", f"/api/v1/accounts/me/keys/{key_id}") == 0.0
        # and the other way around (one DELETE hit was already charged above,
        # so four more fill the 5-hit budget)
        assert _drain("acct-a", "DELETE",
                      f"/api/v1/accounts/me/keys/{key_id}", 4) == 0.0
        assert check_account_budget(
            "acct-a", "DELETE", f"/api/v1/accounts/me/keys/{key_id}") > 0
    finally:
        _unpatch_clock(real)


def test_revoke_dependency_raises_429_with_retry_after():
    _reset_account()
    now, real = _patch_clock()
    account = {"id": "acct-a"}
    key_id = "12345678-1234-5678-1234-567812345678"
    try:
        path = f"/api/v1/accounts/me/keys/{key_id}"
        assert _drain("acct-a", "DELETE", path, 5) == 0.0
        req = _request("DELETE", path)
        with pytest.raises(HTTPException) as exc:
            _run(deps.account_write_budget(req, account))
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers
        # under budget the dependency passes silently
        _reset_account()
        assert _run(deps.account_write_budget(
            _request("DELETE", path), account)) is None
    finally:
        _unpatch_clock(real)


# ---------------------------------------------------------------------------
# malformed key_id -> 404, never 500, never touches the DB
# ---------------------------------------------------------------------------

class _ExplodingPool:
    async def execute(self, *a, **kw):
        raise AssertionError("store must not be reached for malformed key_id")


class _StubPool:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def execute(self, *a, **kw):
        self.calls.append((a, kw))
        return self.result


def test_revoke_malformed_key_id_is_404_without_db():
    for bad in ("not-a-uuid", "", "../etc/passwd", "x" * 64, "12345"):
        with pytest.raises(HTTPException) as exc:
            _run(revoke_key(bad, account={"id": "acct-a"},
                            pool=_ExplodingPool()))
        assert exc.value.status_code == 404
        assert exc.value.detail == "no such key"


def test_revoke_wellformed_uuid_reaches_store_and_succeeds():
    pool = _StubPool("UPDATE 1")
    key_id = "12345678-1234-5678-1234-567812345678"
    out = _run(revoke_key(key_id, account={"id": "acct-a"}, pool=pool))
    assert out == {"ok": True, "revoked": key_id}
    assert len(pool.calls) == 1


def test_revoke_unknown_uuid_is_404():
    pool = _StubPool("UPDATE 0")
    key_id = "12345678-1234-5678-1234-567812345678"
    with pytest.raises(HTTPException) as exc:
        _run(revoke_key(key_id, account={"id": "acct-a"}, pool=pool))
    assert exc.value.status_code == 404
