"""Recoverable 404s on the public skill read paths (Functions lane).

When an agent mistypes a slug, the registry used to return a dead end:
{"detail": "no skill 'regex-mastr'"}. Now unknown slugs get npm-style
"did you mean" suggestions (machine-readable `suggestions` list), and
unknown versions get the `available_versions` list — one round trip to
recover instead of guessing.

Stub-pool style (no DB): assert the store helpers and the router 404
bodies for the five read endpoints (skills x4 + bundle download).

Run:  pytest tests/test_did_you_mean.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

from api.routers import bundles as bundles_router
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


_SLUG_ROWS = [
    {"slug": "regex-mastery"}, {"slug": "api-debugging"},
    {"slug": "note-taking-systems"}, {"slug": "ffmpeg-basics"},
]


def _http_404(coro):
    """Run a router coroutine; return the HTTPException it must raise."""
    try:
        run(coro)
    except HTTPException as exc:
        assert exc.status_code == 404, f"expected 404, got {exc.status_code}"
        return exc
    raise AssertionError("router did not raise HTTPException")


# --- store.suggest_slugs ---------------------------------------------------

def test_suggest_slugs_finds_close_match():
    db = ScriptedDB([_SLUG_ROWS])
    assert "regex-mastery" in run(store.suggest_slugs(db, "regex-mastr"))


def test_suggest_slugs_queries_only_approved():
    db = ScriptedDB([_SLUG_ROWS])
    run(store.suggest_slugs(db, "xyz"))
    query, _ = db.queries[0]
    assert "status = 'approved'" in query


def test_suggest_slugs_empty_when_nothing_close():
    db = ScriptedDB([_SLUG_ROWS])
    assert run(store.suggest_slugs(db, "zzzzzzzz")) == []


def test_suggest_slugs_respects_limit():
    rows = [{"slug": f"regex-test-{i}"} for i in range(10)]
    db = ScriptedDB([rows])
    got = run(store.suggest_slugs(db, "regex-test", limit=2))
    assert len(got) <= 2


def test_list_version_labels_returns_versions_in_order():
    db = ScriptedDB([[ {"version": "1.0.0"}, {"version": "1.1.0"} ]])
    assert run(store.list_version_labels(db, "regex-mastery")) == [
        "1.0.0", "1.1.0"]


# --- router 404 bodies -----------------------------------------------------

def test_get_skill_404_suggests():
    db = ScriptedDB([None, _SLUG_ROWS])  # skill lookup miss, then slugs
    exc = _http_404(skills_router.get_skill("regex-mastr", pool=db))
    assert exc.detail["suggestions"] == ["regex-mastery"]
    assert "did you mean" in exc.detail["message"]
    assert "regex-mastery" in exc.detail["message"]


def test_get_skill_404_no_suggestions_stays_clean():
    db = ScriptedDB([None, _SLUG_ROWS])
    exc = _http_404(skills_router.get_skill("zzzzzzzz", pool=db))
    assert exc.detail["suggestions"] == []
    assert exc.detail["message"] == "no skill 'zzzzzzzz'"


def test_get_version_unknown_slug_suggests():
    db = ScriptedDB([None, None, _SLUG_ROWS])
    exc = _http_404(skills_router.get_version("regex-mastr", "1.0.0", pool=db))
    assert exc.detail["suggestions"] == ["regex-mastery"]


def _skill_row():
    return {"id": "s1", "slug": "regex-mastery", "versions": []}


def test_get_version_unknown_version_lists_available():
    # version miss -> skill hit (row + versions fetch + ratings fetch)
    # -> version labels
    db = ScriptedDB([None, _skill_row(), [], [],
                     [{"version": "1.0.0"}, {"version": "2.0.0"}]])
    exc = _http_404(
        skills_router.get_version("regex-mastery", "9.9.9", pool=db))
    assert exc.detail["available_versions"] == ["1.0.0", "2.0.0"]
    assert "1.0.0" in exc.detail["message"]
    assert "suggestions" not in exc.detail  # version-level, not slug-level


def test_read_skill_md_404_suggests():
    db = ScriptedDB([None, _SLUG_ROWS])
    exc = _http_404(skills_router.read_skill_md("regex-mastr", pool=db))
    assert exc.detail["suggestions"] == ["regex-mastery"]


def test_bundle_404_suggests_on_unknown_slug():
    db = ScriptedDB([None, None, _SLUG_ROWS])
    exc = _http_404(bundles_router.download_bundle("regex-mastr", None,
                                                   pool=db))
    assert exc.detail["suggestions"] == ["regex-mastery"]
