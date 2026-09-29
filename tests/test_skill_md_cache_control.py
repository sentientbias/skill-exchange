"""Cache-Control on GET /api/v1/skills/{slug}/skill.md and
/api/v1/skills/{slug}/versions/{version}/skill.md (npm-tarball convention).

A version row is content-stable (UNIQUE version per skill, no UPDATE path
on skill_versions, the ed25519 signature covers slug+version+skill_md), so
the pinned-version reader stays valid forever and is safe to cache for a
year, marked immutable. The unpinned "latest" reader resolves at request
time and flips on the next publish, so it gets a short 5-minute public
cache only. Headers only: no page, copy, API shape, or Pro-tier change.

Stub-DB introspection style -- no live Postgres needed.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import skills
from core import store


class StubDB:
    pass


def _req(if_none_match=None):
    headers = {}
    if if_none_match is not None:
        headers["if-none-match"] = if_none_match
    return SimpleNamespace(headers=headers)


FAKE_VER = {
    "slug": "fake-skill",
    "version": "1.0.0",
    "skill_md": "# Fake Skill\n" + "x" * 60,
}


def run(coro):
    return asyncio.run(coro)


def _run_latest(monkeypatch):
    async def fake_get_version(db, slug, ver):
        assert slug == "fake-skill"
        assert ver is None
        return dict(FAKE_VER)

    monkeypatch.setattr(store, "get_version", fake_get_version)
    return run(skills.read_skill_md(request=_req(), slug="fake-skill",
                                    pool=StubDB()))


def _run_pinned(monkeypatch):
    async def fake_get_version(db, slug, ver):
        assert slug == "fake-skill"
        assert ver == "1.0.0"
        return dict(FAKE_VER)

    monkeypatch.setattr(store, "get_version", fake_get_version)
    return run(skills.read_version_skill_md(request=_req(), slug="fake-skill",
                                            version="1.0.0", pool=StubDB()))


def test_latest_skill_md_gets_short_cache_only(monkeypatch):
    resp = _run_latest(monkeypatch)
    cc = resp.headers.get("cache-control", "")
    assert "immutable" not in cc, (
        f"unpinned 'latest' must NOT be immutable: {cc!r}")
    assert "max-age=300" in cc, f"latest reader max-age wrong: {cc!r}"
    assert "public" in cc, f"latest reader should be public: {cc!r}"


def test_pinned_version_skill_md_is_immutable_cached(monkeypatch):
    resp = _run_pinned(monkeypatch)
    cc = resp.headers.get("cache-control", "")
    assert "immutable" in cc, f"pinned reader must be immutable: {cc!r}"
    assert "max-age=31536000" in cc, f"pinned reader max-age wrong: {cc!r}"
    assert "public" in cc, f"pinned reader should be public: {cc!r}"


def test_skill_md_still_served_as_markdown_inline(monkeypatch):
    latest = _run_latest(monkeypatch)
    assert latest.media_type == "text/markdown; charset=utf-8"
    assert latest.headers["content-disposition"] == (
        'inline; filename="fake-skill-latest.md"')
    pinned = _run_pinned(monkeypatch)
    assert pinned.headers["content-disposition"] == (
        'inline; filename="fake-skill-1.0.0.md"')
    body = latest.body.decode("utf-8")
    assert body.startswith("# Fake Skill")


def test_unknown_slug_still_404s(monkeypatch):
    async def fake_get_version(db, slug, ver):
        return None

    async def fake_suggest_slugs(db, slug):
        return []

    monkeypatch.setattr(store, "get_version", fake_get_version)
    monkeypatch.setattr(store, "suggest_slugs", fake_suggest_slugs)
    try:
        run(skills.read_skill_md(request=_req(), slug="nope-skill",
                                 pool=StubDB()))
    except Exception as exc:  # HTTPException from _raise_unknown_skill
        assert getattr(exc, "status_code", None) == 404, (
            f"unknown slug must stay 404, got {exc!r}")
    else:
        raise AssertionError("unknown slug should raise, not return")


def test_unknown_version_still_404s(monkeypatch):
    async def fake_get_version(db, slug, ver):
        return None

    async def fake_get_skill(db, slug):
        return {"slug": slug}

    async def fake_list_version_labels(db, slug):
        return ["1.0.0"]

    monkeypatch.setattr(store, "get_version", fake_get_version)
    monkeypatch.setattr(store, "get_skill", fake_get_skill)
    monkeypatch.setattr(store, "list_version_labels",
                        fake_list_version_labels)
    try:
        run(skills.read_version_skill_md(request=_req(), slug="fake-skill",
                                         version="9.9.9", pool=StubDB()))
    except Exception as exc:  # HTTPException from _raise_unknown_version
        assert getattr(exc, "status_code", None) == 404, (
            f"unknown version must stay 404, got {exc!r}")
    else:
        raise AssertionError("unknown version should raise, not return")
