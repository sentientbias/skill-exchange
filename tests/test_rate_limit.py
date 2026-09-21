"""Per-IP rate limiting on anonymous /api/* write endpoints (HTTP 429).

Design under test: the anonymous write endpoints (POST /api/v1/installs,
POST /api/v1/accounts) had no throttle at all -- a single script could mint
unlimited accounts (Sybil fuel for threat #5) or forge install events and
inflate the download counts shown on the front door and skill detail pages.
api/rate_limit.py budgets those two endpoints per client IP with a sliding
window, returning 429 + Retry-After when exhausted.

Run:  pytest tests/test_rate_limit.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.rate_limit as rl
from api.rate_limit import (
    RateLimitMiddleware,
    _budget_for,
    _check,
    _client_ip,
    _reset,
)
from fastapi import Request
from fastapi.responses import JSONResponse


def _run(coro):
    return asyncio.run(coro)


def _scope(method, path, xff=None):
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": ("203.0.113.7", 1234),
    }


def _receive_factory(chunks):
    msgs = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]

    async def receive():
        if msgs:
            return msgs.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


async def _ok_next(request):
    return JSONResponse({"ok": True})


def _dispatch(method, path, xff=None, bucket=None, body=b"{}"):
    """Drive the middleware through a stubbed ASGI request.

    bucket optionally monkeypatches the budget for the path, e.g.
    bucket=(3, 60). Returns the response.
    """
    _reset()
    saved = rl.BUCKETS.get((method, path))
    if bucket is not None:
        rl.BUCKETS[(method, path)] = bucket
    try:
        mw = RateLimitMiddleware(app=None)
        request = Request(
            _scope(method, path, xff), receive=_receive_factory([body])
        )
        return _run(mw.dispatch(request, _ok_next))
    finally:
        if bucket is not None:
            if saved is None:
                rl.BUCKETS.pop((method, path), None)
            else:
                rl.BUCKETS[(method, path)] = saved
        _reset()


def _dispatch_seq(method, path, n, xff=None, budget=(3, 60), now_seq=None):
    """Send n requests in a row with per-call monotonic times now_seq.

    Returns the list of responses. Budget is patched to (method, path).
    """
    _reset()
    saved = rl.BUCKETS.get((method, path))
    rl.BUCKETS[(method, path)] = budget
    real_clock = rl._monotonic
    calls = {"i": 0}

    def fake_monotonic():
        calls["i"] += 1
        if now_seq is not None and calls["i"] <= len(now_seq):
            return now_seq[calls["i"] - 1]
        return 1e9 + calls["i"] * 0.001  # far future: keeps prior hits fresh

    rl._monotonic = fake_monotonic
    try:
        mw = RateLimitMiddleware(app=None)
        resps = []
        for _ in range(n):
            request = Request(
                _scope(method, path, xff), receive=_receive_factory([b"{}"])
            )
            resps.append(_run(mw.dispatch(request, _ok_next)))
        return resps
    finally:
        rl._monotonic = real_clock
        if saved is None:
            rl.BUCKETS.pop((method, path), None)
        else:
            rl.BUCKETS[(method, path)] = saved
        _reset()


# ---------------------------------------------------------------------------
# budget matching
# ---------------------------------------------------------------------------

def test_installs_endpoint_is_budgeted():
    budget = _budget_for("POST", "/api/v1/installs")
    assert budget == (30, 60)


def test_accounts_endpoint_is_budgeted():
    budget = _budget_for("POST", "/api/v1/accounts")
    assert budget == (10, 60)


def test_authenticated_writes_are_not_budgeted():
    assert _budget_for("POST", "/api/v1/skills") is None
    assert _budget_for("POST", "/api/v1/skills/slug/versions") is None
    assert _budget_for("POST", "/api/v1/skills/slug/ratings") is None


def test_reads_are_not_budgeted():
    assert _budget_for("GET", "/api/v1/installs") is None
    assert _budget_for("GET", "/api/v1/skills") is None


# ---------------------------------------------------------------------------
# client IP detection
# ---------------------------------------------------------------------------

def test_xff_first_entry_wins():
    request = Request(_scope("POST", "/api/v1/installs",
                             xff="198.51.100.9, 203.0.113.1"))
    assert _client_ip(request) == "198.51.100.9"


def test_client_host_used_without_xff():
    request = Request(_scope("POST", "/api/v1/installs"))
    assert _client_ip(request) == "203.0.113.7"


# ---------------------------------------------------------------------------
# sliding-window behavior
# ---------------------------------------------------------------------------

def test_under_budget_passes():
    resps = _dispatch_seq("POST", "/api/v1/installs", 3,
                          budget=(3, 60),
                          now_seq=[0.0, 1.0, 2.0])
    assert all(r.status_code == 200 for r in resps)


def test_over_budget_is_429_with_retry_after():
    resps = _dispatch_seq("POST", "/api/v1/installs", 4,
                          budget=(3, 60),
                          now_seq=[0.0, 1.0, 2.0, 3.0])
    assert [r.status_code for r in resps[:3]] == [200, 200, 200]
    last = resps[3]
    assert last.status_code == 429
    assert "retry-after" in {k.lower() for k, v in last.headers.items()}
    import json

    detail = json.loads(last.body)["detail"]
    assert "Rate limit exceeded" in detail


def test_window_expiry_allows_again():
    # 3 allowed at t=0,1,2; the 4th at t=3 is rejected; at t=61 the oldest
    # hit has expired so a request is allowed again.
    resps = _dispatch_seq("POST", "/api/v1/installs", 5,
                          budget=(3, 60),
                          now_seq=[0.0, 1.0, 2.0, 3.0, 61.0])
    assert [r.status_code for r in resps] == [200, 200, 200, 429, 200]


def test_different_ips_have_independent_budgets():
    # budget 1/min on a synthetic path; two IPs each get one clean request
    seq = lambda xff: _dispatch_seq(
        "POST", "/api/v1/installs", 2, xff=xff, budget=(1, 60),
        now_seq=[0.0, 0.5])
    a = seq("198.51.100.1")
    b = seq("198.51.100.2")
    assert a[0].status_code == 200 and a[1].status_code == 429
    assert b[0].status_code == 200 and b[1].status_code == 429


def test_non_api_paths_untouched():
    resp = _dispatch("POST", "/", body=b"x" * 1000)
    assert resp.status_code == 200


def test_unbudgeted_api_write_passes():
    resp = _dispatch("POST", "/api/v1/skills", body=b'{"x": 1}')
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# unit: _check pruning and retry math
# ---------------------------------------------------------------------------

def test_check_returns_zero_and_records():
    _reset()
    assert _check("k", 2, 60.0, now=0.0) == 0.0
    assert _check("k", 2, 60.0, now=10.0) == 0.0
    _reset()


def test_check_retry_after_covers_oldest_hit():
    _reset()
    assert _check("k", 1, 60.0, now=0.0) == 0.0
    retry = _check("k", 1, 60.0, now=10.0)
    assert 49.0 <= retry <= 50.0  # oldest hit at t=0 expires at t=60
    _reset()


def test_check_prunes_expired_hits():
    _reset()
    assert _check("k", 1, 60.0, now=0.0) == 0.0
    assert _check("k", 1, 60.0, now=61.0) == 0.0  # t=0 expired
    _reset()
