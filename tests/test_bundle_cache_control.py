"""Cache-Control on GET /api/v1/bundles/{slug} (npm-tarball convention).

A published version row is content-stable (UNIQUE version per skill, no
UPDATE path on skill_versions, the ed25519 signature covers
slug+version+skill_md), so a pinned-version bundle is safe to cache for a
year, marked immutable. An unpinned "latest" download resolves at request
time and flips on the next publish, so it gets a short 5-minute public
cache only. Headers only: no page, copy, API shape, or Pro-tier change.

Stub-DB introspection style -- no live Postgres needed.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import bundles
from core import store


class StubDB:
    pass


class StubRequest:
    """Minimal request stub: method + plain-dict headers are all the
    download route reads (method for the HEAD branch, if-none-match for
    the 304 short-circuit)."""

    def __init__(self, method="GET", headers=None):
        self.method = method
        self.headers = headers or {}


FAKE_VER = {
    "version": "1.0.0",
    "skill_md": "# Fake Skill\n" + "x" * 60,
    "manifest": {"name": "fake-skill"},
    "signature": "aa" * 64,
    "signer_pubkey": "bb" * 32,
}


def run(coro):
    return asyncio.run(coro)


def _run_download(monkeypatch, version):
    async def fake_get_version(db, slug, ver):
        assert slug == "fake-skill"
        return dict(FAKE_VER)

    async def fake_get_skill(db, slug):
        return {"slug": slug}

    monkeypatch.setattr(store, "get_version", fake_get_version)
    monkeypatch.setattr(store, "get_skill", fake_get_skill)
    return run(bundles.download_bundle(StubRequest(), slug="fake-skill",
                                       version=version, pool=StubDB()))


def test_pinned_version_bundle_is_immutable_cached(monkeypatch):
    resp = _run_download(monkeypatch, version="1.0.0")
    cc = resp.headers.get("cache-control", "")
    assert "immutable" in cc, f"pinned bundle must be immutable: {cc!r}"
    assert "max-age=31536000" in cc, f"pinned bundle max-age wrong: {cc!r}"
    assert "public" in cc, f"pinned bundle should be public: {cc!r}"


def test_latest_bundle_gets_short_cache_only(monkeypatch):
    resp = _run_download(monkeypatch, version=None)
    cc = resp.headers.get("cache-control", "")
    assert "immutable" not in cc, (
        f"unpinned 'latest' must NOT be immutable: {cc!r}")
    assert "max-age=300" in cc, f"latest bundle max-age wrong: {cc!r}"


def test_bundle_still_a_valid_zip_attachment(monkeypatch):
    resp = _run_download(monkeypatch, version="1.0.0")
    assert resp.headers["content-disposition"] == (
        'attachment; filename="fake-skill-1.0.0.zip"')
    assert resp.media_type == "application/zip"

    async def _collect():
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    body = run(_collect())
    assert body[:2] == b"PK", "bundle must still be a zip file"


def test_explicit_latest_alias_bundle_gets_short_cache_only(monkeypatch):
    # ?version=latest resolves at request time (store.get_version treats
    # "latest" like None), so it floats with every publish and must not
    # take the immutable branch.
    resp = _run_download(monkeypatch, version="latest")
    cc = resp.headers.get("cache-control", "")
    assert "immutable" not in cc, (
        f"explicit ?version=latest must NOT be immutable: {cc!r}")
    assert "max-age=300" in cc, f"latest-alias bundle max-age wrong: {cc!r}"
    assert "public" in cc, f"latest-alias bundle should be public: {cc!r}"


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
        run(bundles.download_bundle(StubRequest(), slug="fake-skill",
                                    version="2.0.0", pool=StubDB()))
    except Exception as exc:  # HTTPException from _raise_unknown_version
        assert getattr(exc, "status_code", None) == 404, (
            f"unknown version must stay 404, got {exc!r}")
    else:
        raise AssertionError("unknown version should raise, not return")
