"""ETag + If-None-Match conditional GET on both skill.md readers.

Follows the immutable-Cache-Control pass (npm-tarball convention): the
pinned-version reader is content-stable, so a strong ETag lets repeat
clients revalidate with a 304 instead of re-downloading bytes. The latest
reader's tag flips exactly when a new version becomes latest, so stale
304s are impossible. The tag is an opaque sha256 of slug + version +
the stored ed25519 signature (which already binds slug+version+skill_md),
never the signature itself.

Headers only: no page, copy, API shape, or Pro-tier change.

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


FAKE_SIG = "aa" * 64  # 128 hex chars, ed25519 signature shape

FAKE_VER = {
    "slug": "fake-skill",
    "version": "1.0.0",
    "skill_md": "# Fake Skill\n" + "x" * 60,
    "signature": FAKE_SIG,
}


def run(coro):
    return asyncio.run(coro)


def _req(if_none_match=None):
    headers = {}
    if if_none_match is not None:
        headers["if-none-match"] = if_none_match
    return SimpleNamespace(headers=headers)


def _run_latest(monkeypatch, if_none_match=None, version_row=None):
    async def fake_get_version(db, slug, ver):
        assert slug == "fake-skill"
        assert ver is None
        return dict(version_row or FAKE_VER)

    monkeypatch.setattr(store, "get_version", fake_get_version)
    return run(skills.read_skill_md(request=_req(if_none_match),
                                    slug="fake-skill", pool=StubDB()))


def _run_pinned(monkeypatch, if_none_match=None, version_row=None):
    async def fake_get_version(db, slug, ver):
        assert slug == "fake-skill"
        assert ver == "1.0.0"
        return dict(version_row or FAKE_VER)

    monkeypatch.setattr(store, "get_version", fake_get_version)
    return run(skills.read_version_skill_md(request=_req(if_none_match),
                                            slug="fake-skill", version="1.0.0",
                                            pool=StubDB()))


def test_pinned_reader_emits_strong_etag(monkeypatch):
    resp = _run_pinned(monkeypatch)
    assert resp.status_code == 200
    etag = resp.headers.get("etag", "")
    assert etag.startswith('"') and etag.endswith('"') and len(etag) == 66, (
        f"ETag must be a quoted sha256 hex digest, got {etag!r}")


def test_etag_is_deterministic_per_content(monkeypatch):
    first = _run_pinned(monkeypatch).headers["etag"]
    second = _run_pinned(monkeypatch).headers["etag"]
    assert first == second


def test_etag_changes_when_signature_changes(monkeypatch):
    before = _run_pinned(monkeypatch).headers["etag"]
    row = dict(FAKE_VER, signature="bb" * 64)
    after = _run_pinned(monkeypatch, version_row=row).headers["etag"]
    assert before != after, "content change must move the ETag"


def test_latest_reader_etag_flips_on_new_version(monkeypatch):
    old = _run_latest(monkeypatch).headers["etag"]
    row = dict(FAKE_VER, version="2.0.0", signature="cc" * 64)
    new = _run_latest(monkeypatch, version_row=row).headers["etag"]
    assert old != new, "a newly published latest version must flip the ETag"


def test_etag_does_not_leak_signature(monkeypatch):
    etag = _run_pinned(monkeypatch).headers["etag"]
    assert FAKE_SIG not in etag, "raw signature bytes must not appear in ETag"


def test_matching_if_none_match_returns_304(monkeypatch):
    etag = _run_pinned(monkeypatch).headers["etag"]
    resp = _run_pinned(monkeypatch, if_none_match=etag)
    assert resp.status_code == 304, f"expected 304, got {resp.status_code}"
    assert resp.body == b"", "304 must carry no body"
    assert resp.headers["etag"] == etag, "304 must echo the ETag"
    assert "max-age=31536000" in resp.headers.get("cache-control", ""), (
        "304 should keep the pinned Cache-Control")


def test_star_if_none_match_returns_304(monkeypatch):
    resp = _run_latest(monkeypatch, if_none_match="*")
    assert resp.status_code == 304


def test_weak_validator_matches(monkeypatch):
    etag = _run_pinned(monkeypatch).headers["etag"]
    resp = _run_pinned(monkeypatch, if_none_match=f"W/{etag}")
    assert resp.status_code == 304, "W/\"tag\" must match \"tag\" (weak)"


def test_non_matching_if_none_match_returns_200(monkeypatch):
    resp = _run_pinned(monkeypatch, if_none_match='"deadbeef"')
    assert resp.status_code == 200
    assert resp.body.decode("utf-8").startswith("# Fake Skill")


def test_latest_304_keeps_short_cache(monkeypatch):
    etag = _run_latest(monkeypatch).headers["etag"]
    resp = _run_latest(monkeypatch, if_none_match=etag)
    assert resp.status_code == 304
    cc = resp.headers.get("cache-control", "")
    assert "max-age=300" in cc and "immutable" not in cc


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
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404, (
            f"unknown slug must stay 404, got {exc!r}")
    else:
        raise AssertionError("unknown slug should raise, not return")
