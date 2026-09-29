"""Per-account rate limiting on authenticated writes (HTTP 429).

Design under test: the authenticated write endpoints -- skill publishes,
new versions, ratings, and API-key minting -- had no rate budget at all, so
a stolen or abused API key (threat 6) could mint keys without limit, spam
publishes into the moderation queue (reviewer DoS), and machine-gun ratings.
The anonymous per-IP middleware in api.rate_limit runs before auth and never
sees these endpoints. api.deps.account_write_budget runs after
current_account and charges each hit against
api.rate_limit.ACCOUNT_BUCKETS, keyed per account: 30 writes / 60 s on the
POST /api/v1/skills prefix (publishes + ratings), 5 key mints / 60 s.

Run:  pytest tests/test_account_rate_limit.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.rate_limit as rl
from api import deps
from api.rate_limit import _reset_account, check_account_budget
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
    """Charge n hits; return the first nonzero retry_after seen (0 if none)."""
    for _ in range(n):
        retry = check_account_budget(account, method, path)
        if retry > 0:
            return retry
    return 0.0


def test_publish_budget_allows_then_limits():
    _reset_account()
    now, real = _patch_clock()
    try:
        assert _drain("acct-a", "POST", "/api/v1/skills", 30) == 0.0
        retry = check_account_budget("acct-a", "POST", "/api/v1/skills")
        assert retry > 0
    finally:
        _unpatch_clock(real)


def test_budget_window_reopens():
    _reset_account()
    now, real = _patch_clock()
    try:
        assert _drain("acct-a", "POST", "/api/v1/skills", 30) == 0.0
        assert check_account_budget("acct-a", "POST", "/api/v1/skills") > 0
        now[0] += 61.0  # window expires
        assert check_account_budget("acct-a", "POST", "/api/v1/skills") == 0.0
    finally:
        _unpatch_clock(real)


def test_budgets_are_per_account():
    _reset_account()
    now, real = _patch_clock()
    try:
        assert _drain("acct-a", "POST", "/api/v1/skills", 30) == 0.0
        assert check_account_budget("acct-a", "POST", "/api/v1/skills") > 0
        # a different account starts with a fresh budget
        assert check_account_budget("acct-b", "POST", "/api/v1/skills") == 0.0
    finally:
        _unpatch_clock(real)


def test_versions_and_ratings_share_publish_prefix_bucket():
    _reset_account()
    now, real = _patch_clock()
    try:
        # 15 publishes + 15 version publishes + ratings share one 30/min bucket
        assert _drain("acct-a", "POST", "/api/v1/skills", 15) == 0.0
        assert _drain("acct-a", "POST", "/api/v1/skills/x/versions", 15) == 0.0
        assert check_account_budget("acct-a", "POST", "/api/v1/skills/x/ratings") > 0
    finally:
        _unpatch_clock(real)


def test_key_minting_has_own_tighter_budget():
    _reset_account()
    now, real = _patch_clock()
    try:
        assert _drain("acct-a", "POST", "/api/v1/accounts/me/keys", 5) == 0.0
        assert check_account_budget("acct-a", "POST", "/api/v1/accounts/me/keys") > 0
        # exhausting the key bucket does not touch the publish bucket
        assert check_account_budget("acct-a", "POST", "/api/v1/skills") == 0.0
    finally:
        _unpatch_clock(real)


def test_non_budgeted_route_is_never_limited():
    _reset_account()
    now, real = _patch_clock()
    try:
        for _ in range(200):
            assert check_account_budget("acct-a", "GET", "/api/v1/skills") == 0.0
            assert check_account_budget("acct-a", "GET", "/api/v1/accounts/me") == 0.0
    finally:
        _unpatch_clock(real)


def test_dependency_raises_429_with_retry_after_over_budget():
    _reset_account()
    now, real = _patch_clock()
    account = {"id": "acct-a"}
    try:
        req = _request("POST", "/api/v1/skills")
        _run(deps.account_write_budget(req, account=account))  # under budget: fine
        _drain("acct-a", "POST", "/api/v1/skills", 29)
        try:
            _run(deps.account_write_budget(req, account=account))
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.headers and "Retry-After" in exc.headers
            assert int(exc.headers["Retry-After"]) >= 1
        else:
            raise AssertionError("expected HTTPException 429")
    finally:
        _unpatch_clock(real)


def test_dependency_passes_non_budgeted_route():
    _reset_account()
    account = {"id": "acct-a"}
    req = _request("GET", "/api/v1/skills")
    assert _run(deps.account_write_budget(req, account=account)) is None


def test_account_id_used_in_bucket_key_not_ip():
    # Two accounts behind the same IP must not share a budget.
    _reset_account()
    now, real = _patch_clock()
    try:
        assert _drain("acct-a", "POST", "/api/v1/accounts/me/keys", 5) == 0.0
        assert check_account_budget("acct-a", "POST", "/api/v1/accounts/me/keys") > 0
        assert check_account_budget("acct-z", "POST", "/api/v1/accounts/me/keys") == 0.0
    finally:
        _unpatch_clock(real)
