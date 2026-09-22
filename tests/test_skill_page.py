"""Per-skill detail page (GET /skills/{slug}): package-page standard.

Design under test: every registry (npm, PyPI, Hugging Face Hub) gives each
package a canonical human-readable page -- install command, version
history, provenance, ratings. The Playbook's front-door cards used to link
straight to a raw SKILL.md download (a dead end for a deciding visitor);
they now link here.

Run:  pytest tests/test_skill_page.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.skill_page import (
    PRO_URL,
    PLAYBOOK_URL,
    skill_not_found_html,
    skill_page_html,
)

RAW_DOMAIN = "x402-seller-a5et.onrender.com"

_ROW = {
    "id": "00000000-0000-0000-0000-000000000000",
    "name": "Regex Mastery",
    "slug": "regex-mastery",
    "description": "Practical regular expressions for agents.",
    "category": "devtools",
    "publisher": "zuckbot",
    "latest_version": "1.2.0",
    "avg_stars": 4.5,
    "rating_count": 4,
    "downloads": 8,
    "created_at": "2026-09-17 12:00:00",
}
_VERSIONS = [
    {
        "version": "1.0.0",
        "created_at": "2026-09-10 12:00:00",
        "downloads": 3,
        "signature": "c2lnMQ==",
        "signer_pubkey": "cHVia2V5QUJD",
    },
    {
        "version": "1.2.0",
        "created_at": "2026-09-17 12:00:00",
        "downloads": 5,
        "signature": "c2lnMg==",
        "signer_pubkey": "cHVia2V5QUJD",
    },
]
_RATINGS = [
    {
        "stars": 5,
        "handle": "mikey",
        "created_at": "2026-09-18 09:00:00",
        "comment": "Solved my parsing problem in an afternoon.",
    },
    {
        "stars": 4,
        "handle": "wynjr",
        "created_at": "2026-09-18 10:00:00",
        "comment": "",
    },
]


def _full_skill():
    skill = dict(_ROW)
    skill["versions"] = [dict(v) for v in _VERSIONS]
    skill["recent_ratings"] = [dict(r) for r in _RATINGS]
    return skill


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# pure HTML builder
# ---------------------------------------------------------------------------

def test_detail_page_renders_package_page_basics():
    page = skill_page_html(_full_skill())
    assert "Regex Mastery" in page
    assert "v1.2.0" in page
    assert "devtools" in page
    assert "zuckbot" in page  # publisher provenance, like npm/PyPI
    assert "Practical regular expressions for agents." in page
    assert "<b>8</b> install" in page
    assert "★ 4.5 (4)" in page
    assert "Ed25519-signed" in page


def test_detail_page_has_install_command_and_downloads():
    page = skill_page_html(_full_skill())
    assert "./install.sh regex-mastery" in page
    assert "/api/v1/bundles/regex-mastery" in page
    assert "/api/v1/skills/regex-mastery/skill.md" in page
    assert "/api/v1/skills/regex-mastery" in page  # machine JSON


def test_detail_page_lists_versions_and_pubkey():
    page = skill_page_html(_full_skill())
    assert "v1.0.0" in page
    assert "v1.2.0" in page
    assert "cHVia2V5QUJD" in page  # signer pubkey (already public via receipts)


def test_detail_page_version_rows_are_actionable():
    # npm's Versions tab gives every release its own copy-paste install
    # line (npm i pkg@x.y.z) and tarball link; the Playbook row does the
    # same via the already-existing [version] arg on install.sh and the
    # already-existing ?version= on the bundle endpoint.
    page = skill_page_html(_full_skill())
    assert "./install.sh regex-mastery 1.0.0" in page
    assert "./install.sh regex-mastery 1.2.0" in page
    assert "/api/v1/bundles/regex-mastery?version=1.0.0" in page
    assert "/api/v1/bundles/regex-mastery?version=1.2.0" in page


def test_version_row_escapes_version_and_slug():
    from api.skill_page import _version_row

    row = _version_row(
        {
            "version": '1.0"><script>alert(1)</script>',
            "created_at": "2026-09-10 12:00:00",
            "downloads": 0,
        },
        'bad"><script>alert(2)</script>',
    )
    assert "<script>" not in row
    assert "&lt;script&gt;" in row
    # URL quoting makes the raw payload inert even in the href
    assert '">alert' not in row


def test_detail_page_lists_ratings():
    page = skill_page_html(_full_skill())
    assert "Solved my parsing problem in an afternoon." in page
    assert "mikey" in page
    assert "★★★★★" in page
    assert "★★★★☆" in page


def test_detail_page_degrades_without_ratings_or_versions():
    skill = _full_skill()
    skill["versions"] = []
    skill["recent_ratings"] = []
    skill["avg_stars"] = 0.0
    skill["rating_count"] = 0
    page = skill_page_html(skill)
    assert "No ratings yet" in page
    assert "no ratings yet" in page


def test_xss_content_is_escaped():
    skill = _full_skill()
    skill["name"] = '<script>alert(1)</script>'
    skill["description"] = 'desc "quoted" <b>bold</b>'
    skill["publisher"] = 'evil"><img src=x onerror=alert(1)>'
    skill["recent_ratings"] = [
        {
            "stars": 1,
            "handle": "<i>hax</i>",
            "created_at": "2026-09-18 09:00:00",
            "comment": "<script>alert(2)</script>",
        }
    ]
    page = skill_page_html(skill)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<b>bold</b>" not in page
    assert "<i>hax</i>" not in page
    assert "<script>alert(2)</script>" not in page


def test_detail_page_follows_canonical_link_policy():
    page = skill_page_html(_full_skill())
    assert PLAYBOOK_URL in page
    assert PRO_URL in page
    assert RAW_DOMAIN not in page


def test_not_found_page_escapes_slug_and_links_home():
    page = skill_not_found_html('nope"><script>alert(1)</script>')
    assert "<script>" not in page
    assert 'href="/"' in page
    assert "nope" in page


# ---------------------------------------------------------------------------
# route handler (with stub pools -- no live Postgres needed)
# ---------------------------------------------------------------------------

class _GoodPool:
    async def fetchrow(self, query, *args):
        row = dict(_ROW)
        if "from skill_versions v" in query:
            # store.get_version's query: the route attaches this SKILL.md
            row["skill_md"] = _MD_BODY
        return row

    async def fetch(self, query, *args):
        if "skill_versions" in query:
            return [dict(v) for v in _VERSIONS]
        return [dict(r) for r in _RATINGS]


class _EmptyPool:
    async def fetchrow(self, query, *args):
        return None

    async def fetch(self, query, *args):
        return []


class _DeadPool:
    async def fetchrow(self, query, *args):
        raise ConnectionError("db is down")

    async def fetch(self, query, *args):
        raise ConnectionError("db is down")


def test_route_serves_detail_page():
    from api.main import skill_detail

    resp = _run(skill_detail(slug="regex-mastery", pool=_GoodPool()))
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Regex Mastery" in body
    assert "./install.sh regex-mastery" in body
    assert RAW_DOMAIN not in body
    # route attaches the latest SKILL.md; the README section renders inline
    assert "Skill contents" in body
    assert "<h3>Regex Mastery</h3>" in body
    assert "<strong>extracting data</strong>" in body


def test_route_404_on_unknown_slug():
    from api.main import skill_detail

    resp = _run(skill_detail(slug="no-such-skill", pool=_EmptyPool()))
    assert resp.status_code == 404
    assert "No such skill" in resp.body.decode()


def test_route_404_not_500_on_invalid_slug():
    from api.main import skill_detail

    resp = _run(skill_detail(slug="Bad Slug!", pool=_GoodPool()))
    assert resp.status_code == 404


def test_route_404_not_500_on_db_error():
    from api.main import skill_detail

    resp = _run(skill_detail(slug="regex-mastery", pool=_DeadPool()))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# inline SKILL.md (registry README convention)
# ---------------------------------------------------------------------------

_MD_BODY = """# Regex Mastery

Use this skill when **extracting data** from logs or *rewriting text*.

## Patterns

- `\\d{4}-\\d{2}-\\d{2}` — ISO dates
- Timestamps and UUIDs

```python
import re
re.findall(r"\\d+", text)
```

1. First step
2. Second step

See [docs](https://example.com/guide) for more.
"""

_MD_EVIL = (
    "<script>alert(1)</script>\n\n"
    '[x](javascript:alert(2))\n\n'
    '```html\n<script>alert(3)</script>\n```\n'
)


def _skill_with_md(md):
    skill = _full_skill()
    skill["latest_skill_md"] = md
    return skill


def test_skill_md_rendered_inline():
    page = skill_page_html(_skill_with_md(_MD_BODY))
    assert '<div class="skillmd">' in page
    assert "<h2>Skill contents</h2>" in page
    assert "<h3>Regex Mastery</h3>" in page
    assert "<strong>extracting data</strong>" in page
    assert "<em>rewriting text</em>" in page
    assert "<h4>Patterns</h4>" in page
    assert "<ul>" in page and "<li>" in page
    assert "<pre><code" in page
    assert '<a href="https://example.com/guide">docs</a>' in page
    assert "<ol>" in page
    # raw "Read the SKILL.md" link still present for exact-copy use
    assert "/api/v1/skills/regex-mastery/skill.md" in page


def test_skill_md_uses_route_attached_body():
    skill = _skill_with_md(_MD_BODY)
    page = skill_page_html(skill)
    assert "Regex Mastery" in page
    assert '<div class="skillmd">' in page


def test_skill_md_missing_degrades():
    skill = _full_skill()  # _VERSIONS rows have no skill_md key
    page = skill_page_html(skill)
    assert '<div class="skillmd">' not in page
    assert "Skill contents" not in page
    # page still fully renders
    assert "v1.2.0" in page


def test_skill_md_xss_is_inert():
    page = skill_page_html(_skill_with_md(_MD_EVIL))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "javascript:" not in page
    assert "<script>alert(3)</script>" not in page  # inside code fence too
    assert "&lt;script&gt;alert(3)&lt;/script&gt;" in page


def test_skill_md_soft_wraps_and_hr():
    from api.skill_page import _render_markdown
    out = _render_markdown("- first line\n  wrapped continuation\n\n---\n")
    assert out.count("<li>") == 1
    assert "first line wrapped continuation" in out
    assert "<hr>" in out
    assert "<p>---</p>" not in out
