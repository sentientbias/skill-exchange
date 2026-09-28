"""`since` parity on GET /api/v1/bundles.

/api/v1/skills has had an ISO-8601 `since` filter for a while; it is what
powers "what's new" polling for agent clients. GET /api/v1/bundles — the
feed agents actually poll for installable updates — silently dropped it,
so bundle update loops had to re-diff the whole catalog every pass.

The fix adds the same param (same validator, same 422-on-garbage gate)
and passes it through to store.list_skills, which already filters on it.

These tests use a stub DB and introspection — no live Postgres needed.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from fastapi.routing import APIRoute

from api import query_params
from api.routers import bundles, skills
from core import store


class StubDB:
    pass


class StubRequest:
    base_url = "https://example.com/"


def run(coro):
    return asyncio.run(coro)


def _query_param(router, path, name):
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path:
            for param in route.dependant.query_params:
                if param.name == name:
                    return param
    raise AssertionError(f"no {name!r} query param found on {path!r}")


def test_bundles_list_declares_since_param():
    param = _query_param(bundles.router, "/bundles", "since")
    assert param.field_info.default == "", "since must default to empty (no filtering)"


def test_bundles_and_skills_share_the_same_since_validator():
    # The 422-on-garbage gate must not silently drift between the two feeds.
    assert bundles.validate_since is query_params.validate_since
    assert skills.validate_since is query_params.validate_since


def _run_list_bundles(since, monkeypatch):
    rows = [{"slug": "regex-mastery", "name": "Regex Mastery",
             "latest_version": "1.0.0"}]
    seen = {}

    async def fake_list_skills(db, **kwargs):
        seen.update(kwargs)
        return rows

    async def fake_count_skills(db, **kwargs):
        return len(rows)

    monkeypatch.setattr(store, "list_skills", fake_list_skills)
    monkeypatch.setattr(store, "count_skills", fake_count_skills)
    result = run(bundles.list_bundles(
        request=StubRequest(), q="", category="", limit=20, offset=0,
        since=since, pool=StubDB(),
    ))
    return result, seen


def test_bundles_since_passthrough(monkeypatch):
    result, seen = _run_list_bundles("2026-09-01T00:00:00Z", monkeypatch)
    assert seen.get("since") == "2026-09-01T00:00:00Z", (
        f"since not passed to store.list_skills: {seen!r}"
    )
    assert result["items"][0]["slug"] == "regex-mastery"
    assert result["items"][0]["download_url"].startswith("https://example.com/")


def test_bundles_since_empty_means_unfiltered(monkeypatch):
    _, seen = _run_list_bundles("", monkeypatch)
    assert seen.get("since") == "", "empty since must reach the store untouched"


def test_bundles_since_rejects_garbage_before_db(monkeypatch):
    called = {"hit": False}

    async def fake_list_skills(db, **kwargs):
        called["hit"] = True
        return []

    monkeypatch.setattr(store, "list_skills", fake_list_skills)
    try:
        run(bundles.list_bundles(
            request=StubRequest(), q="", category="", limit=20, offset=0,
            since="yesterday", pool=StubDB(),
        ))
    except HTTPException as e:
        assert e.status_code == 422, e
    else:
        raise AssertionError("no 422 for garbage since on /bundles")
    assert not called["hit"], "DB was hit before the since gate rejected the value"
