"""Canonical-slug discipline for download filenames and zip entries.

Defense in depth: zip entry paths, Content-Disposition filenames, and the
receipt's slug must come from the DB-canonical slug (ver["slug"], selected
as s.slug from the skills table), never from the raw request path
parameter. Today's strict slug regex in store._check_slug makes injection
through the request slug unexploitable -- these tests stub the store so the
request slug differs from the DB slug, simulating a future where the slug
alphabet widens (or a new route forgets the check) and proving the routers
still emit canonical values everywhere a client-visible name is built.

Stub-DB introspection style -- no live Postgres needed.
"""
import asyncio
import io
import json
import os
import sys
import zipfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import bundles
from api.routers import skills as skills_router
from core import store


class StubDB:
    pass


# The request slug (lookup key) and the DB-canonical slug deliberately
# differ -- this is the future/regression scenario the fix guards against.
REQUEST_SLUG = 'weird"slug;x'
CANONICAL_SLUG = "canonical-skill"

FAKE_VER = {
    "slug": CANONICAL_SLUG,
    "version": "1.0.0",
    "skill_md": "# Fake Skill\n" + "x" * 60,
    "manifest": {"name": "fake-skill"},
    "signature": "aa" * 64,
    "signer_pubkey": "bb" * 32,
}


def run(coro):
    return asyncio.run(coro)


def _stub_store(monkeypatch):
    async def fake_get_version(db, slug, ver, **kw):
        assert slug == REQUEST_SLUG
        return dict(FAKE_VER)

    async def fake_get_skill(db, slug):
        return {"slug": CANONICAL_SLUG}

    monkeypatch.setattr(store, "get_version", fake_get_version)
    monkeypatch.setattr(store, "get_skill", fake_get_skill)


def _zip_body(resp):
    async def _collect():
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    return run(_collect())


def test_bundle_filename_uses_canonical_slug(monkeypatch):
    _stub_store(monkeypatch)
    resp = run(bundles.download_bundle(slug=REQUEST_SLUG, version="1.0.0",
                                       pool=StubDB()))
    cd = resp.headers["content-disposition"]
    assert REQUEST_SLUG not in cd, (
        f"request slug leaked into Content-Disposition: {cd!r}")
    assert cd == 'attachment; filename="canonical-skill-1.0.0.zip"', cd


def test_bundle_zip_entries_use_canonical_slug(monkeypatch):
    _stub_store(monkeypatch)
    resp = run(bundles.download_bundle(slug=REQUEST_SLUG, version="1.0.0",
                                       pool=StubDB()))
    names = zipfile.ZipFile(io.BytesIO(_zip_body(resp))).namelist()
    assert names == [
        "canonical-skill/SKILL.md",
        "canonical-skill/manifest.json",
        "canonical-skill/receipt.json",
    ], f"zip entries must use the canonical slug, got {names!r}"


def test_bundle_receipt_carries_canonical_slug(monkeypatch):
    _stub_store(monkeypatch)
    resp = run(bundles.download_bundle(slug=REQUEST_SLUG, version="1.0.0",
                                       pool=StubDB()))
    zf = zipfile.ZipFile(io.BytesIO(_zip_body(resp)))
    receipt = json.loads(zf.read("canonical-skill/receipt.json"))
    assert receipt["slug"] == CANONICAL_SLUG, (
        f"receipt slug must be canonical, got {receipt['slug']!r}")


def test_skill_md_filename_uses_canonical_slug(monkeypatch):
    _stub_store(monkeypatch)
    resp = run(skills_router.read_skill_md(
        request=SimpleNamespace(headers={}), slug=REQUEST_SLUG,
        pool=StubDB()))
    cd = resp.headers["content-disposition"]
    assert cd == 'inline; filename="canonical-skill-latest.md"', cd


def test_version_skill_md_filename_uses_canonical_slug(monkeypatch):
    _stub_store(monkeypatch)
    resp = run(skills_router.read_version_skill_md(
        request=SimpleNamespace(headers={}), slug=REQUEST_SLUG,
        version="1.0.0", pool=StubDB()))
    cd = resp.headers["content-disposition"]
    assert cd == 'inline; filename="canonical-skill-1.0.0.md"', cd


def test_request_slug_fallback_when_row_lacks_slug(monkeypatch):
    """Hand-built dicts without 'slug' (older tests, edge paths) still work:
    the request slug is used, preserving prior behavior."""
    async def fake_get_version(db, slug, ver, **kw):
        ver_dict = dict(FAKE_VER)
        del ver_dict["slug"]
        return ver_dict

    async def fake_get_skill(db, slug):
        return {"slug": slug}

    monkeypatch.setattr(store, "get_version", fake_get_version)
    monkeypatch.setattr(store, "get_skill", fake_get_skill)
    resp = run(bundles.download_bundle(slug="fake-skill", version="1.0.0",
                                       pool=StubDB()))
    assert resp.headers["content-disposition"] == (
        'attachment; filename="fake-skill-1.0.0.zip"')
