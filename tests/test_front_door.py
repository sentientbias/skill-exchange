"""Front door (GET /): live catalog strips + graceful DB fallback.

Design under test: the front page server-renders real catalog content
(newest + most-installed, live stats) like HF Hub / PyPI "trending
projects" homepages — no JS, so plain-HTTP agent clients read it too.

Run:  pytest tests/test_front_door.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.front_door import PRO_URL, PLAYBOOK_URL, front_door_html

RAW_DOMAIN = "x402-seller-a5et.onrender.com"

_SAMPLE = [
    {
        "name": "Regex Mastery",
        "slug": "regex-mastery",
        "description": "Practical regular expressions for agents.",
        "category": "devtools",
        "latest_version": "1.2.0",
        "avg_stars": 4.5,
        "rating_count": 4,
        "downloads": 8,
    },
    {
        "name": "Cold Email Drafting",
        "slug": "cold-email-drafting",
        "description": "Draft cold emails that get replies.",
        "category": "writing",
        "latest_version": "1.0.0",
        "avg_stars": 0.0,
        "rating_count": 0,
        "downloads": 7,
    },
]
_STATS = {"skill_count": 50, "total_downloads": 31}


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# pure HTML builder
# ---------------------------------------------------------------------------

def test_live_mode_renders_strips_and_stats():
    page = front_door_html(_SAMPLE, _SAMPLE, _STATS)
    assert "New in the library" in page
    assert "Most installed" in page
    assert "Regex Mastery" in page
    assert "/api/v1/skills/regex-mastery/skill.md" in page
    assert "<b>50</b> skills" in page
    assert "<b>31</b> installs" in page
    assert "Ed25519-signed" in page
    # ratings line: 4.5 avg / 4 ratings; unrated skill shows "no ratings yet"
    assert "★ 4.5 (4)" in page
    assert "no ratings yet" in page
    assert "v1.2.0" in page


def test_xss_content_is_escaped():
    evil = [{
        "name": "<script>alert(1)</script>",
        "slug": 'evil"><img src=x onerror=alert(1)>',
        "description": 'desc "quoted" <b>bold</b>',
        "category": "devtools",
        "latest_version": "1.0.0",
        "avg_stars": 5.0,
        "rating_count": 1,
        "downloads": 0,
    }]
    page = front_door_html(evil, evil, _STATS)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<b>bold</b>" not in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page
    assert 'evil&quot;&gt;' in page  # slug escaped inside href


def test_degraded_mode_still_renders_hero_and_cards():
    page = front_door_html(None, None, None)
    assert "The <span>Playbook</span>" in page
    assert "New in the library" not in page
    assert "Most installed" not in page
    assert "For agents" in page
    assert "How to publish" in page


def test_outbound_links_follow_canonical_policy():
    for args in [(_SAMPLE, _SAMPLE, _STATS), (None, None, None)]:
        page = front_door_html(*args)
        assert PLAYBOOK_URL in page
        assert PRO_URL in page
        assert RAW_DOMAIN not in page


# ---------------------------------------------------------------------------
# route handler (with stub pools — no live Postgres needed)
# ---------------------------------------------------------------------------

class _GoodPool:
    async def fetch(self, query, *args):
        return [dict(s) for s in _SAMPLE]

    async def fetchrow(self, query, *args):
        return dict(_STATS)


class _DeadPool:
    async def fetch(self, query, *args):
        raise ConnectionError("db is down")

    async def fetchrow(self, query, *args):
        raise ConnectionError("db is down")


def test_index_serves_live_page():
    from api.main import index

    resp = _run(index(pool=_GoodPool()))
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Regex Mastery" in body
    assert "New in the library" in body
    assert RAW_DOMAIN not in body


def test_index_degrades_on_db_error():
    from api.main import index

    resp = _run(index(pool=_DeadPool()))
    assert resp.status_code == 200  # never 500 the front door
    body = resp.body.decode()
    assert "The <span>Playbook</span>" in body
    assert "New in the library" not in body
