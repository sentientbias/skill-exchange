"""HEAD + conditional GET on GET /api/v1/bundles/{slug} (download freshness).

The bundle download was the last content-stable read without the
freshness protocol the skill.md readers already have (ETag + If-None-Match
304s + HEAD): every "did the bundle change?" check forced a full zip
download. Now a matching ETag returns 304 before the zip is ever built,
and HEAD answers existence + metadata with no body.

Stub-DB style -- no live Postgres needed.
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


def _run(monkeypatch, method="GET", headers=None, version="1.0.0"):
    async def fake_get_version(db, slug, ver):
        assert slug == "fake-skill"
        return dict(FAKE_VER)

    async def fake_get_skill(db, slug):
        return {"slug": slug}

    monkeypatch.setattr(store, "get_version", fake_get_version)
    monkeypatch.setattr(store, "get_skill", fake_get_skill)
    return run(bundles.download_bundle(StubRequest(method=method,
                                                   headers=headers),
                                       slug="fake-skill", version=version,
                                       pool=StubDB()))


def _zip_bytes(resp):
    async def _collect():
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    return run(_collect())


def test_head_is_registered_on_bundle_route():
    paths = {(r.path, tuple(sorted(getattr(r, "methods", ()) or ())))
             for r in bundles.router.routes}
    assert ("/bundles/{slug}", ("GET", "HEAD")) in paths, (
        f"HEAD missing from route methods: {paths}")


def test_head_returns_metadata_with_empty_body(monkeypatch):
    resp = _run(monkeypatch, method="HEAD")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"] == (
        'attachment; filename="fake-skill-1.0.0.zip"')
    assert "immutable" in resp.headers["cache-control"]
    assert resp.headers.get("etag", "").startswith('"'), (
        f"HEAD must carry a quoted ETag: {dict(resp.headers)}")


def test_head_omits_content_length_because_zip_bytes_vary(monkeypatch):
    # The zip embeds request-time metadata (receipt.json fetched_at) whose
    # deflate output is not byte-stable across requests (two consecutive
    # builds measured 688 vs 687 bytes), so HEAD must not advertise a
    # measured zip byte count. Starlette stamps bodyless responses with
    # content-length: 0; the test asserts no real byte count leaks.
    get_resp = _run(monkeypatch, method="GET")
    body = _zip_bytes(get_resp)
    assert body[:2] == b"PK"
    assert len(body) > 0
    head_resp = _run(monkeypatch, method="HEAD")
    assert head_resp.status_code == 200
    assert head_resp.headers.get("content-length") == "0", (
        "HEAD must carry no measured zip byte count, only Starlette's "
        "bodyless 0")


def test_head_etag_matches_get_etag(monkeypatch):
    get_resp = _run(monkeypatch, method="GET")
    head_resp = _run(monkeypatch, method="HEAD")
    assert head_resp.headers["etag"] == get_resp.headers["etag"], (
        "freshness parity: HEAD and GET must emit the same ETag")


def test_get_with_matching_etag_returns_304(monkeypatch):
    first = _run(monkeypatch, method="GET")
    etag = first.headers["etag"]
    resp = _run(monkeypatch, method="GET",
                headers={"if-none-match": etag})
    assert resp.status_code == 304
    assert resp.headers["etag"] == etag
    assert resp.headers["cache-control"] == first.headers["cache-control"]


def test_head_with_matching_etag_returns_304(monkeypatch):
    first = _run(monkeypatch, method="GET")
    resp = _run(monkeypatch, method="HEAD",
                headers={"if-none-match": first.headers["etag"]})
    assert resp.status_code == 304
    assert resp.headers["etag"] == first.headers["etag"]


def test_weak_etag_match_returns_304(monkeypatch):
    first = _run(monkeypatch, method="GET")
    resp = _run(monkeypatch, method="GET",
                headers={"if-none-match": "W/" + first.headers["etag"]})
    assert resp.status_code == 304


def test_stale_etag_returns_full_body(monkeypatch):
    resp = _run(monkeypatch, method="GET",
                headers={"if-none-match": '"deadbeef"'})
    assert resp.status_code == 200
    assert _zip_bytes(resp)[:2] == b"PK"


def test_head_pinned_is_immutable_unpinned_is_short(monkeypatch):
    head_pinned = _run(monkeypatch, method="HEAD", version="1.0.0")
    assert "immutable" in head_pinned.headers["cache-control"]
    head_latest = _run(monkeypatch, method="HEAD", version=None)
    cc = head_latest.headers["cache-control"]
    assert "immutable" not in cc
    assert "max-age=300" in cc


def test_head_unknown_slug_404s_with_suggestions(monkeypatch):
    async def fake_get_version(db, slug, ver):
        return None

    async def fake_get_skill(db, slug):
        return None

    async def fake_suggest(db, slug, limit=3):
        return ["fake-skill"]

    monkeypatch.setattr(store, "get_version", fake_get_version)
    monkeypatch.setattr(store, "get_skill", fake_get_skill)
    monkeypatch.setattr(store, "suggest_slugs", fake_suggest)
    try:
        run(bundles.download_bundle(StubRequest(method="HEAD"),
                                    slug="fake-skill", version="1.0.0",
                                    pool=StubDB()))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404, (
            f"unknown slug must stay 404, got {exc!r}")
        assert "fake-skill" in str(getattr(exc, "detail", "")), (
            "HEAD 404 should still carry the did-you-mean suggestion")
    else:
        raise AssertionError("unknown slug should raise, not return")


def test_head_unknown_version_404s(monkeypatch):
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
        run(bundles.download_bundle(StubRequest(method="HEAD"),
                                    slug="fake-skill", version="2.0.0",
                                    pool=StubDB()))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404, (
            f"unknown version must stay 404, got {exc!r}")
    else:
        raise AssertionError("unknown version should raise, not return")
