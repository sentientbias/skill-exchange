"""Case-insensitive slug lookups on the public read paths (Functions lane).

An agent that sees the display name "Discord Server Growth" (or copies a
slug with the wrong case) and requests /api/v1/skills/Discord-Server-Growth
used to get a dead end: 400 {"detail": "slug must match ^[a-z0-9]..."}.
Slugs are stored lowercase, so the registry now lowercases the slug at the
store read boundary (get_skill, get_version, rate_skill, suggest_slugs,
list_version_labels) -- npm/PyPI convention -- and the request resolves to
the skill instead. WRITE paths (create_skill, create_version, delist/relist)
stay strict: publishers must sign exactly what they publish.

Headers/API-shape only on error paths; no page, copy, or Pro-tier change.

Stub-pool style (no DB): assert the normalized slug is what reaches the
query, mixed-case inputs resolve, invalid slugs still raise, and publish
still rejects uppercase slugs before touching the DB.

Run:  pytest tests/test_slug_case_insensitive.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import skills as skills_router
from core import store


class ScriptedDB:
    """Hand back canned fetch/fetchrow results in call order."""

    def __init__(self, script):
        self.script = list(script)
        self.queries = []

    async def fetch(self, query, *params):
        self.queries.append((query, params))
        result = self.script.pop(0)
        assert isinstance(result, list), "fetch expected a list script item"
        return result

    async def fetchrow(self, query, *params):
        self.queries.append((query, params))
        result = self.script.pop(0)
        assert not isinstance(result, list), "fetchrow expected a row"
        return result


def run(coro):
    return asyncio.run(coro)


_SKILL_ROW = {
    "id": str(uuid4()),
    "slug": "discord-server-growth",
    "name": "Discord Server Growth",
    "description": "Grow your server",
    "category": "media",
    "author_account_id": str(uuid4()),
    "status": "approved",
    "latest_version_id": str(uuid4()),
    "created_at": "2026-09-30T23:40:00Z",
    "updated_at": "2026-09-30T23:40:00Z",
    "avg_stars": 4.5,
    "rating_count": 2,
    "total_downloads": 10,
}

_VER_ROW = {
    "id": str(uuid4()),
    "slug": "discord-server-growth",
    "version": "1.0.0",
    "skill_md": "# Discord\n" + "x" * 60,
    "manifest": {},
    "signature": "aa" * 64,
    "signer_pubkey": "bb" * 32,
    "downloads": 10,
    "created_at": "2026-09-30T23:40:00Z",
}


def _skill_db():
    # get_skill issues: fetchrow (skill), fetch (versions), fetch (ratings).
    return ScriptedDB([dict(_SKILL_ROW), [{"version": "1.0.0"}], []])


def _version_db():
    # get_version latest: one fetchrow.
    return ScriptedDB([dict(_VER_ROW)])


# --- get_skill normalizes ----------------------------------------------------

def test_get_skill_lowercases_mixed_case():
    db = _skill_db()
    skill = run(store.get_skill(db, "Discord-Server-Growth"))
    assert skill["slug"] == "discord-server-growth"
    query, params = db.queries[0]
    assert params == ("discord-server-growth",)


def test_get_skill_strips_whitespace():
    db = _skill_db()
    run(store.get_skill(db, "  discord-server-growth  "))
    assert db.queries[0][1] == ("discord-server-growth",)


def test_get_skill_rejects_invalid_slug():
    for bad in ("", "   ", "not a slug!", "a" * 45, "-leading"):
        try:
            run(store.get_skill(ScriptedDB([]), bad))
        except ValueError as exc:
            assert "slug must match" in str(exc)
        else:
            raise AssertionError(f"{bad!r} did not raise")


# --- get_version normalizes --------------------------------------------------

def test_get_version_latest_lowercases():
    db = _version_db()
    ver = run(store.get_version(db, "Discord-Server-Growth", None))
    assert ver["version"] == "1.0.0"
    assert db.queries[0][1] == ("discord-server-growth",)


def test_get_version_pinned_lowercases():
    db = ScriptedDB([dict(_VER_ROW)])
    run(store.get_version(db, "DISCORD-SERVER-GROWTH", "1.0.0"))
    assert db.queries[0][1] == ("discord-server-growth", "1.0.0")


# --- rate_skill normalizes ---------------------------------------------------

def test_rate_skill_lowercases_before_lookup():
    author = str(uuid4())
    rater = str(uuid4())
    row = dict(_SKILL_ROW)
    row["author_account_id"] = author
    db = ScriptedDB([row, {"id": str(uuid4()), "stars": 5}])
    run(store.rate_skill(db, rater, "Discord-Server-Growth", 5, "great"))
    query, params = db.queries[0]
    assert params == ("discord-server-growth",)


# --- suggest_slugs is case-robust --------------------------------------------

def test_suggest_slugs_lowercases_input():
    rows = [{"slug": "discord-server-growth"}, {"slug": "regex-mastery"}]
    db = ScriptedDB([rows])
    assert "discord-server-growth" in run(
        store.suggest_slugs(db, "Discord-Server-Grwoth"))


# --- router path: mixed case resolves through the real router ----------------

def test_router_skill_detail_accepts_mixed_case(monkeypatch):
    # The router hands the raw path slug to store; the REAL store
    # normalizes inside get_skill. The fake mirrors that so the test
    # asserts the end-to-end resolution an agent gets.
    seen = {}

    async def fake_get_skill(db, slug, **kwargs):
        seen["raw"] = slug
        slug = slug.strip().lower()
        assert slug == "discord-server-growth"
        seen["normalized"] = slug
        return {"slug": slug, "name": "Discord"}

    monkeypatch.setattr(store, "get_skill", fake_get_skill)
    out = run(skills_router.get_skill(slug="Discord-Server-Growth",
                                      pool=object()))
    assert out["slug"] == "discord-server-growth"
    assert seen["raw"] == "Discord-Server-Growth"
    assert seen["normalized"] == "discord-server-growth"


def test_router_skill_md_reader_filename_uses_db_canonical(monkeypatch):
    async def fake_get_version(db, slug, ver):
        slug = slug.strip().lower()  # mirrors the real store boundary
        assert slug == "discord-server-growth"
        return dict(_VER_ROW)

    monkeypatch.setattr(store, "get_version", fake_get_version)
    req = SimpleNamespace(headers={}, method="GET")
    resp = run(skills_router.read_skill_md(request=req,
                                           slug="Discord-Server-Growth",
                                           pool=object()))
    disp = resp.headers["content-disposition"]
    assert "discord-server-growth-latest.md" in disp
    assert "Discord" not in disp


# --- write paths stay strict -------------------------------------------------

def test_create_skill_still_rejects_uppercase():
    db = ScriptedDB([])
    try:
        run(store.create_skill(
            db, str(uuid4()), name="X", slug="Discord-Server-Growth",
            description="x", category="general", version="1.0.0",
            skill_md="x" * 60, manifest={},
            signature="a" * 88, public_key="b" * 64,
        ))
    except ValueError as exc:
        assert "slug must match" in str(exc)
    else:
        raise AssertionError("create_skill accepted an uppercase slug")
    assert db.queries == [], "publish must not touch the DB on a bad slug"


def test_create_version_still_rejects_uppercase():
    db = ScriptedDB([])
    try:
        run(store.create_version(
            db, str(uuid4()), "Discord-Server-Growth", version="1.0.0",
            skill_md="x" * 60, manifest={},
            signature="a" * 88, public_key="b" * 64,
        ))
    except ValueError as exc:
        assert "slug must match" in str(exc)
    else:
        raise AssertionError("create_version accepted an uppercase slug")
    assert db.queries == [], "publish must not touch the DB on a bad slug"
