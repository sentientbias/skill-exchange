"""Per-IP budgets on anonymous API GET reads (HTTP 429).

Design under test: the JSON reads had per-request fast-fail caps
(length-bounded q, clamped offsets, validated sorts) but no *volume*
budget -- a single client could hammer the per-slug detail/raw-markdown
readers and the bundle downloads (a zip is built per request) with no
cost. api/rate_limit.py now budgets the anonymous API reads at the same
human-speed 120/60s as the HTML pages: /api/v1/skills/ and
/api/v1/bundles/ by per-slug prefix, the bare list reads and /api/v1/stats
by exact match. Authenticated API reads (bearer-key gated, e.g.
/api/v1/accounts/me) stay deliberately unbudgeted.

Run:  pytest tests/test_api_read_rate_limit.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.rate_limit as rl
from api.rate_limit import RateLimitMiddleware, _matched, _reset
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

    budget monkeypatches the bucket for this (method, path), so tests do
    not need 120 real requests. Returns the response of the LAST request
    after exhausting (budget[0] + 1) hits.
    """
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = None
    saved = None
    saved_exact = None
    if budget is not None:
        norm = "GET" if method == "HEAD" else method
        if (norm, path) in rl.EXACT_BUCKETS:
            key = (norm, path)
            saved_exact = rl.EXACT_BUCKETS[key]
            rl.EXACT_BUCKETS[key] = budget
        else:
            m = _matched(norm, path)
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
            if key in rl.EXACT_BUCKETS:
                rl.EXACT_BUCKETS[key] = saved_exact
            else:
                if saved is None:
                    del rl.BUCKETS[key]
                else:
                    rl.BUCKETS[key] = saved


def test_api_skill_read_prefix_budgeted():
    resp = _dispatch("GET", "/api/v1/skills/regex-mastery/skill.md",
                    budget=(3, 60))
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_api_version_read_prefix_budgeted():
    resp = _dispatch("GET", "/api/v1/skills/regex-mastery/versions/1.0.0",
                    budget=(3, 60))
    assert resp.status_code == 429


def test_api_bundle_download_prefix_budgeted():
    # The zip-build route: the one per-request CPU cost on anonymous reads.
    resp = _dispatch("GET", "/api/v1/bundles/regex-mastery", budget=(3, 60))
    assert resp.status_code == 429


def test_api_skills_list_exact_budgeted():
    resp = _dispatch("GET", "/api/v1/skills", budget=(3, 60))
    assert resp.status_code == 429


def test_api_bundles_list_exact_budgeted():
    resp = _dispatch("GET", "/api/v1/bundles", budget=(3, 60))
    assert resp.status_code == 429


def test_api_stats_exact_budgeted():
    resp = _dispatch("GET", "/api/v1/stats", budget=(3, 60))
    assert resp.status_code == 429


def test_head_shares_api_get_budget():
    # Exhaust the skill.md GET bucket with GETs, then a HEAD must 429 too.
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = ("GET", "/api/v1/skills/")
    saved = rl.BUCKETS[key]
    rl.BUCKETS[key] = (2, 60)
    try:
        for _ in range(2):
            scope = _scope("GET", "/api/v1/skills/regex-mastery/skill.md")
            resp = _run(mw.dispatch(Request(scope, _receive_factory()), _ok_next))
            assert resp.status_code == 200
        head_scope = _scope("HEAD", "/api/v1/skills/regex-mastery/skill.md")
        head_resp = _run(
            mw.dispatch(Request(head_scope, _receive_factory()), _ok_next))
        assert head_resp.status_code == 429
    finally:
        rl.BUCKETS[key] = saved


def test_authenticated_api_reads_stay_unbudgeted():
    # GET /api/v1/accounts/me is bearer-key gated; the deliberate
    # no-budget stance on authenticated reads holds.
    resp = _dispatch("GET", "/api/v1/accounts/me", budget=None)
    assert resp.status_code == 200


def test_api_read_budgets_are_per_ip():
    # Two different client IPs get independent buckets: one IP exhausts
    # its own budget while the other still passes. Done in one flow (no
    # reset between requests) so the independence is actually exercised.
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = ("GET", "/api/v1/skills/")
    saved = rl.BUCKETS[key]
    rl.BUCKETS[key] = (1, 60)
    try:
        path = "/api/v1/skills/regex-mastery/skill.md"
        first = _run(mw.dispatch(
            Request(_scope("GET", path, xff="198.51.100.9"),
                    _receive_factory()), _ok_next))
        assert first.status_code == 200
        second = _run(mw.dispatch(
            Request(_scope("GET", path, xff="198.51.100.9"),
                    _receive_factory()), _ok_next))
        assert second.status_code == 429
        other_ip = _run(mw.dispatch(
            Request(_scope("GET", path, xff="198.51.100.10"),
                    _receive_factory()), _ok_next))
        assert other_ip.status_code == 200
    finally:
        rl.BUCKETS[key] = saved


def test_junk_path_under_prefix_shares_bucket():
    # The middleware runs before route matching: junk suffixes under a
    # budgeted prefix share the prefix's bucket (no fresh-bucket minting).
    _reset()
    mw = RateLimitMiddleware(app=None)
    key = ("GET", "/api/v1/bundles/")
    saved = rl.BUCKETS[key]
    rl.BUCKETS[key] = (2, 60)
    try:
        for path in ("/api/v1/bundles/nope1", "/api/v1/bundles/nope2"):
            resp = _run(mw.dispatch(
                Request(_scope("GET", path), _receive_factory()), _ok_next))
            assert resp.status_code == 200
        resp = _run(mw.dispatch(
            Request(_scope("GET", "/api/v1/bundles/nope3"),
                    _receive_factory()), _ok_next))
        assert resp.status_code == 429
    finally:
        rl.BUCKETS[key] = saved
