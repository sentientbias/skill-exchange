"""ETag + conditional GET on GET /feed.xml (feed freshness).

The RSS feed was the last public read without the freshness protocol the
skill.md readers (ETag/HEAD) and bundle downloads (ETag/HEAD/304) already
have: every \"did anything new publish?\" poll forced a full XML
re-download and re-parse. Now the feed emits a strong ETag over the exact
rendered bytes plus Cache-Control, and a matching If-None-Match returns
304 before the body is sent.

Stub-DB style -- no live Postgres needed.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import feed
from core import store


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
        "created_at": datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
    },
    {
        "slug": "other-skill",
        "name": "Other Skill",
        "description": "Another fake skill.",
        "latest_version": "2.3.1",
        "created_at": datetime(2026, 9, 2, 8, 30, 0, tzinfo=timezone.utc),
    },
]


def run(coro):
    return asyncio.run(coro)


def _run(monkeypatch, headers=None, skills=None):
    items = FAKE_SKILLS if skills is None else skills

    async def fake_list_skills(db, sort=None, limit=None):
        assert sort == "newest"
        assert limit == 20
        return [dict(s) for s in items]

    monkeypatch.setattr(store, "list_skills", fake_list_skills)
    return run(feed.rss_feed(StubRequest(headers=headers), pool=StubDB()))


def _body_text(resp):
    return resp.body.decode("utf-8")


def test_feed_200_with_etag_and_cache_control(monkeypatch):
    resp = _run(monkeypatch)
    assert resp.status_code == 200
    etag = resp.headers.get("etag")
    assert etag is not None and etag.startswith('"') and etag.endswith('"')
    # 64 hex chars between the quotes: sha256 over the rendered body.
    assert len(etag.strip('"')) == 64
    int(etag.strip('"'), 16)
    assert resp.headers.get("cache-control") == "public, max-age=300"


def test_feed_body_still_valid_rss(monkeypatch):
    body = _body_text(_run(monkeypatch))
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<rss version=\"2.0\">" in body
    assert "Fake Skill" in body
    assert "guid isPermaLink=\"false\">skill:fake-skill:1.0.0</guid>" in body


def test_feed_etag_deterministic(monkeypatch):
    etag1 = _run(monkeypatch).headers["etag"]
    etag2 = _run(monkeypatch).headers["etag"]
    assert etag1 == etag2


def test_feed_etag_changes_when_content_changes(monkeypatch):
    etag1 = _run(monkeypatch).headers["etag"]
    changed = [dict(FAKE_SKILLS[0], description="Edited description.")]
    changed.append(dict(FAKE_SKILLS[1]))
    etag2 = _run(monkeypatch, skills=changed).headers["etag"]
    assert etag1 != etag2


def test_feed_matching_etag_304(monkeypatch):
    etag = _run(monkeypatch).headers["etag"]
    resp = _run(monkeypatch, headers={"if-none-match": etag})
    assert resp.status_code == 304
    assert resp.body == b""
    assert resp.headers["etag"] == etag
    assert resp.headers["cache-control"] == "public, max-age=300"


def test_feed_weak_etag_match_304(monkeypatch):
    etag = _run(monkeypatch).headers["etag"]
    resp = _run(monkeypatch, headers={"if-none-match": "W/" + etag})
    assert resp.status_code == 304


def test_feed_star_match_304(monkeypatch):
    resp = _run(monkeypatch, headers={"if-none-match": "*"})
    assert resp.status_code == 304


def test_feed_stale_etag_200_with_body(monkeypatch):
    resp = _run(monkeypatch, headers={"if-none-match": '"0" * 64'})
    assert resp.status_code == 200
    assert b"<rss version=\"2.0\">" in resp.body


def test_feed_empty_catalog_still_304s(monkeypatch):
    etag = _run(monkeypatch, skills=[]).headers["etag"]
    resp = _run(monkeypatch, skills=[], headers={"if-none-match": etag})
    assert resp.status_code == 304
    assert resp.headers["etag"] == etag
