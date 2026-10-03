"""GET /sitemap.xml and GET /robots.txt (crawler discovery surfaces).

The registry had an RSS feed but no sitemap or robots.txt, so search
engines and discovery crawlers had to page /browse or the list API to
find the skill pages. /sitemap.xml now enumerates the front door,
/browse, and every approved skill page (with honest <lastmod> from
updated_at, omitted rather than fabricated when unparseable); /robots.txt
points at the sitemap. Both are new machine-readable surfaces only: no
page, copy, layout, or Pro-tier change.

Both routes carry per-IP budgets (60/60s for the DB-backed sitemap,
120/60s for the static robots.txt), ETag + 304 on the sitemap, and HEAD
shares the GET budget via the middleware's method normalization.

Stub-DB style -- no live Postgres needed.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.rate_limit as rl
from api.rate_limit import RateLimitMiddleware, _reset
from api.routers import feed
from core import store
from fastapi import Request
from fastapi.responses import JSONResponse


class StubDB:
    pass


class StubRequest:
    def __init__(self, method="GET", headers=None):
        self.method = method
        self.headers = headers or {}


FAKE_SKILLS = [
    {
        "slug": "fake-skill",
        "name": "Fake Skill",
        "description": "A fake skill for tests.",
        "latest_version": "1.0.0",
        "updated_at": datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc),
    },
    {
        "slug": "other-skill",
        "name": "Other Skill",
        "description": "Another fake skill.",
        "latest_version": "2.3.1",
        "updated_at": "not-a-timestamp",
    },
]


def run(coro):
    return asyncio.run(coro)


def _run_sitemap(monkeypatch, headers=None, skills=None):
    items = FAKE_SKILLS if skills is None else skills

    async def fake_list_skills(db, sort=None, limit=None, offset=None):
        assert sort == "newest"
        # store.list_skills clamps each request to 100 rows; the sitemap
        # walks offset pages to stay complete as the catalog grows.
        assert limit == 100
        start = offset or 0
        return [dict(s) for s in items[start:start + limit]]

    monkeypatch.setattr(store, "list_skills", fake_list_skills)
    return run(feed.sitemap(StubRequest(headers=headers), pool=StubDB()))


def _body_text(resp):
    return resp.body.decode("utf-8")


def test_sitemap_200_valid_xml_with_all_urls(monkeypatch):
    resp = _run_sitemap(monkeypatch)
    assert resp.status_code == 200
    body = _body_text(resp)
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' in body
    assert "<loc>https://skill-exchange-api-hoev.onrender.com/</loc>" in body
    assert "<loc>https://skill-exchange-api-hoev.onrender.com/browse</loc>" in body
    assert "<loc>https://skill-exchange-api-hoev.onrender.com/skills/fake-skill</loc>" in body
    assert "<loc>https://skill-exchange-api-hoev.onrender.com/skills/other-skill</loc>" in body


def test_sitemap_lastmod_honest(monkeypatch):
    body = _body_text(_run_sitemap(monkeypatch))
    # Real timestamp becomes <lastmod>; garbage becomes no <lastmod> at all.
    assert "<lastmod>2026-10-01</lastmod>" in body
    assert body.count("<lastmod>") == 1


def test_sitemap_walks_offset_pages(monkeypatch):
    # 150 skills: the 100-row per-request clamp must not truncate the sitemap.
    many = [
        dict(slug=f"skill-{i:03d}",
             updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        for i in range(150)
    ]
    body = _body_text(_run_sitemap(monkeypatch, skills=many))
    assert body.count("<url>") == 152  # home + browse + 150 skills
    assert "/skills/skill-149</loc>" in body


def test_sitemap_etag_and_304(monkeypatch):
    resp = _run_sitemap(monkeypatch)
    etag = resp.headers.get("etag")
    assert etag and etag.startswith('"') and etag.endswith('"')
    assert len(etag.strip('"')) == 64
    assert resp.headers.get("cache-control") == "public, max-age=3600"
    # Deterministic, and a matching If-None-Match gets a 304.
    assert _run_sitemap(monkeypatch).headers["etag"] == etag
    resp304 = _run_sitemap(monkeypatch, headers={"if-none-match": etag})
    assert resp304.status_code == 304
    assert resp304.body == b""
    assert resp304.headers["etag"] == etag


def test_robots_txt_static_and_correct():
    resp = run(feed.robots_txt())
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.body.decode("utf-8")
    assert "User-agent: *" in body
    assert "Allow: /" in body
    assert "Sitemap: https://skill-exchange-api-hoev.onrender.com/sitemap.xml" in body


# --- rate-limit budgets (middleware dispatch, stubbed ASGI) ---

def _scope(method, path):
    return {
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


def _receive_factory():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


async def _ok_next(request):
    return JSONResponse({"ok": True})


def _dispatch(method, path, budget):
    """Drive the middleware; returns the response after budget[0] + 1 hits."""
    _reset()
    mw = RateLimitMiddleware(app=None)
    norm = "GET" if method == "HEAD" else method
    m = rl._matched(norm, path)
    assert m is not None, f"no bucket matched for {method} {path}"
    key = (norm, m[0])
    saved = rl.BUCKETS.get(key)
    rl.BUCKETS[key] = budget
    try:
        resp = None
        for _ in range(budget[0] + 1):
            resp = run(mw.dispatch(Request(_scope(method, path), _receive_factory()), _ok_next))
        return resp
    finally:
        if saved is None:
            del rl.BUCKETS[key]
        else:
            rl.BUCKETS[key] = saved


def test_sitemap_bucket_registered():
    prefix, budget = rl._matched("GET", "/sitemap.xml")
    assert prefix == "/sitemap.xml"
    assert budget == (60, 60)


def test_robots_bucket_registered():
    prefix, budget = rl._matched("GET", "/robots.txt")
    assert prefix == "/robots.txt"
    assert budget == (120, 60)


def test_sitemap_get_budgeted():
    resp = _dispatch("GET", "/sitemap.xml", budget=(2, 60))
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_robots_get_budgeted():
    resp = _dispatch("GET", "/robots.txt", budget=(2, 60))
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_sitemap_head_shares_get_budget():
    resp = _dispatch("HEAD", "/sitemap.xml", budget=(2, 60))
    assert resp.status_code == 429
