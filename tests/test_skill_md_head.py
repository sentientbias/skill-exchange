"""HEAD support on both skill.md readers.

Installer scripts, MCP fetch loops, and cache tooling probe existence and
freshness with HEAD; both readers previously answered 405. HEAD now returns
the same metadata as GET (ETag, Cache-Control, Content-Disposition,
Content-Type) plus a correct Content-Length, with an empty body.
If-None-Match still applies, so a matching tag gets a 304 without the
client ever downloading bytes.

Headers only: no page, copy, API shape, or Pro-tier change.

Stub-DB introspection style -- no live Postgres needed.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import skills
from core import store


class StubDB:
    pass


FAKE_SIG = "bb" * 64  # 128 hex chars, ed25519 signature shape

FAKE_VER = {
    "slug": "fake-skill",
    "version": "1.0.0",
    # Non-ASCII content: byte length (not char length) is what matters.
    "skill_md": "# Fake Skill\nsnowman: \u2603\n" + "x" * 40,
    "signature": FAKE_SIG,
}


def run(coro):
    return asyncio.run(coro)


def _req(method="GET", if_none_match=None):
    headers = {}
    if if_none_match is not None:
        headers["if-none-match"] = if_none_match
    return SimpleNamespace(headers=headers, method=method)


def _stub_get_version(monkeypatch, row):
    async def fake_get_version(db, slug, ver):
        return dict(row) if row is not None else None

    monkeypatch.setattr(store, "get_version", fake_get_version)


_USE_FAKE = object()  # sentinel: stub the fake version row


def _head_latest(monkeypatch, if_none_match=None, row=_USE_FAKE):
    _stub_get_version(monkeypatch, FAKE_VER if row is _USE_FAKE else row)
    return run(skills.read_skill_md(request=_req("HEAD", if_none_match),
                                    slug="fake-skill", pool=StubDB()))


def _head_pinned(monkeypatch, if_none_match=None, row=_USE_FAKE):
    _stub_get_version(monkeypatch, FAKE_VER if row is _USE_FAKE else row)
    return run(skills.read_version_skill_md(request=_req("HEAD", if_none_match),
                                            slug="fake-skill",
                                            version="1.0.0", pool=StubDB()))


def _get_latest(monkeypatch):
    _stub_get_version(monkeypatch, FAKE_VER)
    return run(skills.read_skill_md(request=_req("GET"),
                                    slug="fake-skill", pool=StubDB()))


def test_routes_register_head():
    """Both readers must route HEAD, not just GET."""
    paths = {}
    for route in skills.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path and "/skill.md" in path:
            paths[path] = set(methods)
    assert "/skills/{slug}/skill.md" in paths, paths
    assert "/skills/{slug}/versions/{version}/skill.md" in paths, paths
    for path, methods in paths.items():
        assert {"GET", "HEAD"} <= methods, f"{path}: {methods}"


def test_head_latest_returns_metadata_no_body(monkeypatch):
    resp = _head_latest(monkeypatch)
    assert resp.status_code == 200
    assert resp.body == b"", "HEAD must carry no body"
    etag = resp.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"') and len(etag) == 66
    assert resp.headers["cache-control"] == "public, max-age=300"
    assert resp.headers["content-type"] == "text/markdown; charset=utf-8"
    assert resp.headers["content-disposition"] == (
        'inline; filename="fake-skill-latest.md"')
    expected = len(FAKE_VER["skill_md"].encode("utf-8"))
    assert resp.headers["content-length"] == str(expected), (
        "Content-Length must be the byte length of the GET body")


def test_head_pinned_returns_metadata_no_body(monkeypatch):
    resp = _head_pinned(monkeypatch)
    assert resp.status_code == 200
    assert resp.body == b""
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert resp.headers["content-disposition"] == (
        'inline; filename="fake-skill-1.0.0.md"')
    assert resp.headers["content-length"] == str(
        len(FAKE_VER["skill_md"].encode("utf-8")))


def test_head_etag_matches_get_etag(monkeypatch):
    """Freshness parity: HEAD and GET report the same validator."""
    head = _head_latest(monkeypatch)
    get = _get_latest(monkeypatch)
    assert head.headers["etag"] == get.headers["etag"]
    # Sanity: the GET still carries its body.
    assert len(get.body) == int(head.headers["content-length"])


def test_head_with_matching_if_none_match_is_304(monkeypatch):
    etag = _head_latest(monkeypatch).headers["etag"]
    resp = _head_latest(monkeypatch, if_none_match=etag)
    assert resp.status_code == 304
    assert resp.body == b""
    assert resp.headers["etag"] == etag


def test_head_with_stale_if_none_match_is_200(monkeypatch):
    resp = _head_latest(monkeypatch, if_none_match='"deadbeef"')
    assert resp.status_code == 200
    assert resp.body == b""


def test_head_unknown_slug_still_404s(monkeypatch):
    async def fake_suggest(db, slug):
        return ["fake-skill"]

    monkeypatch.setattr(store, "suggest_slugs", fake_suggest)
    with pytest.raises(HTTPException) as excinfo:
        _head_latest(monkeypatch, row=None)
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail["suggestions"] == ["fake-skill"]


def test_head_unknown_version_still_404s(monkeypatch):
    async def fake_get_skill(db, slug):
        return {"slug": "fake-skill"}

    async def fake_list_versions(db, slug):
        return ["1.0.0"]

    monkeypatch.setattr(store, "get_skill", fake_get_skill)
    monkeypatch.setattr(store, "list_version_labels", fake_list_versions)
    with pytest.raises(HTTPException) as excinfo:
        _head_pinned(monkeypatch, row=None)
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail["available_versions"] == ["1.0.0"]
