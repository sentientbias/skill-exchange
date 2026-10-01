"""Per-IP budgets on anonymous page GETs (HTTP 429).

Design under test: the old middleware gated everything on the /api/ prefix,
so the human-facing HTML pages (/browse, /skills/{slug}, the front door,
/install.sh, /playbook-mcp.py, /feed.xml) had no rate budget at all -- a
single client could hammer the DB-backed renders with no cost. api/rate_limit.py
now budgets those anonymous page GETs per client IP (human-speed allowances),
HEAD shares the GET budget, and the front door "/" is exact-matched so the
budget cannot swallow authenticated API reads (deliberately unbudgeted).

Run:  pytest tests/test_page_get_rate_limit.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.rate_limit as rl
from api.rate_limit import RateLimitMiddleware, _reset
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


def _receive_factory():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


async def _ok_next(request):
    return JSONResponse({"ok": True})


def _dispatch(method, path, xff=None, budget=None):
    """Drive the middleware through a stubbed ASGI request.

    budget monkeypatches the budget for this (method, path) bucket, e.g.
    budget=(3, 60), so tests do not need 120 real requests. Returns the
    response of the LAST request after exhausting (budget[0] + 1) hits.
    """
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = None
    if budget is not None:
        # Find the bucket key the middleware would use for this request.
        norm = "GET" if method == "HEAD" else method
        if (norm, path) in rl.EXACT_BUCKETS:
            key = (norm, path)
            saved_exact = rl.EXACT_BUCKETS[key]
            rl.EXACT_BUCKETS[key] = budget
        else:
            m = rl._matched(norm, path)
            assert m is not None, f"no bucket matched for {method} {path}"
            key = (norm, m[0])
            saved = rl.BUCKETS.get(key)
            rl.BUCKETS[key] = budget
    try:
        resp = None
        n = (budget[0] + 1) if budget else 1
        for _ in range(n):
            scope = _scope(method, path, xff)
            resp = _run(mw.dispatch(Request(scope, _receive_factory()), _ok_next))
        return resp
    finally:
        if budget is not None:
            if (key in rl.EXACT_BUCKETS):
                rl.EXACT_BUCKETS[key] = saved_exact
            else:
                if saved is None:
                    del rl.BUCKETS[key]
                else:
                    rl.BUCKETS[key] = saved


def test_browse_get_budgeted():
    resp = _dispatch("GET", "/browse", budget=(3, 60))
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_skill_page_get_budgeted():
    resp = _dispatch("GET", "/skills/regex-mastery", budget=(3, 60))
    assert resp.status_code == 429


def test_front_door_exact_match_budgeted():
    resp = _dispatch("GET", "/", budget=(3, 60))
    assert resp.status_code == 429


def test_front_door_exact_does_not_swallow_api_reads():
    # "/" as a prefix would match /api/v1/...; the exact match must not.
    # The API reads carry their OWN exact budget, not the front door's:
    # exhaust the front-door bucket, then prove an API read still passes.
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = ("GET", "/")
    saved = rl.EXACT_BUCKETS[key]
    rl.EXACT_BUCKETS[key] = (2, 60)
    try:
        for _ in range(2):
            scope = _scope("GET", "/")
            resp = _run(mw.dispatch(Request(scope, _receive_factory()), _ok_next))
            assert resp.status_code == 200
        assert _run(mw.dispatch(
            Request(_scope("GET", "/"), _receive_factory()), _ok_next)
        ).status_code == 429
        # The API list read has its own exact bucket -- still passing here.
        resp = _run(mw.dispatch(
            Request(_scope("GET", "/api/v1/skills"), _receive_factory()),
            _ok_next))
        assert resp.status_code == 200
        # Authenticated reads are still deliberately unbudgeted.
        resp = _run(mw.dispatch(
            Request(_scope("GET", "/api/v1/accounts/me"), _receive_factory()),
            _ok_next))
        assert resp.status_code == 200
    finally:
        rl.EXACT_BUCKETS[key] = saved


def test_head_shares_get_budget():
    # Exhaust the /browse GET bucket with GETs, then a HEAD must 429 too.
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = ("GET", "/browse")
    saved = rl.BUCKETS[key]
    rl.BUCKETS[key] = (2, 60)
    try:
        for _ in range(2):
            scope = _scope("GET", "/browse")
            resp = _run(mw.dispatch(Request(scope, _receive_factory()), _ok_next))
            assert resp.status_code == 200
        head_scope = _scope("HEAD", "/browse")
        head_resp = _run(mw.dispatch(Request(head_scope, _receive_factory()), _ok_next))
        assert head_resp.status_code == 429
    finally:
        rl.BUCKETS[key] = saved


def test_head_alone_counts_against_get_bucket():
    # HEAD requests charge the same bucket as GETs (no bypass).
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = ("GET", "/browse")
    saved = rl.BUCKETS[key]
    rl.BUCKETS[key] = (2, 60)
    try:
        for _ in range(2):
            scope = _scope("HEAD", "/browse")
            resp = _run(mw.dispatch(Request(scope, _receive_factory()), _ok_next))
            assert resp.status_code == 200
        get_scope = _scope("GET", "/browse")
        get_resp = _run(mw.dispatch(Request(get_scope, _receive_factory()), _ok_next))
        assert get_resp.status_code == 429
    finally:
        rl.BUCKETS[key] = saved


def test_install_sh_and_mcp_budgeted():
    assert _dispatch("GET", "/install.sh", budget=(2, 60)).status_code == 429
    assert _dispatch("GET", "/playbook-mcp.py", budget=(2, 60)).status_code == 429
    assert _dispatch("GET", "/feed.xml", budget=(2, 60)).status_code == 429


def test_unrelated_paths_untouched():
    # Routes with no budget pass straight through, whatever the method.
    assert _dispatch("GET", "/no-such-page").status_code == 200
    resp = _dispatch("POST", "/skills/whatever", budget=None)
    assert resp.status_code == 200


def test_429_carries_retry_after_and_json():
    resp = _dispatch("GET", "/browse", budget=(1, 60))
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    import json

    body = json.loads(resp.body.decode())
    assert "Rate limit exceeded" in body["detail"]


def test_budgets_are_per_ip():
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = ("GET", "/browse")
    saved = rl.BUCKETS[key]
    rl.BUCKETS[key] = (1, 60)
    try:
        a = _run(mw.dispatch(Request(_scope("GET", "/browse", "10.0.0.1"), _receive_factory()), _ok_next))
        b = _run(mw.dispatch(Request(_scope("GET", "/browse", "10.0.0.1"), _receive_factory()), _ok_next))
        c = _run(mw.dispatch(Request(_scope("GET", "/browse", "10.0.0.2"), _receive_factory()), _ok_next))
        assert a.status_code == 200
        assert b.status_code == 429
        assert c.status_code == 200
    finally:
        rl.BUCKETS[key] = saved
