"""Recoverable 404s on POST /api/v1/installs (Functions lane).

The install-report endpoint used to answer every lookup miss with the same
flat string: {"detail": "no version '1.0.0' of 'regx-mastery'"}, even when
the skill itself did not exist at all. An agent chasing that message looks
for a version problem when the real problem is a typo'd slug.

Now the two failure modes are distinguished, using the same shapes as the
read endpoints:
- unknown slug -> {"message": "no skill 'x'; did you mean: 'y'?",
  "suggestions": [...]}
- unknown version of a known skill ->
  {"message": "no version 'v' of 'x'; available versions: ...",
   "available_versions": [...]}

Stub-pool style (no DB): script fetch/fetchrow/execute in call order and
call the router coroutine directly.

Run:  pytest tests/test_installs_404.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

from api.routers import ratings as ratings_router
from api.schemas import InstallIn


class ScriptedDB:
    """Hand back canned fetch/fetchrow results in call order; record executes."""

    def __init__(self, script):
        self.script = list(script)
        self.executes = []

    async def fetch(self, query, *params):
        result = self.script.pop(0)
        assert isinstance(result, list), "fetch expected a list script item"
        return result

    async def fetchrow(self, query, *params):
        result = self.script.pop(0)
        assert not isinstance(result, list), "fetchrow expected a row"
        return result

    async def execute(self, query, *params):
        self.executes.append((query, params))
        return "EXECUTE 1"


def run(coro):
    return asyncio.run(coro)


def _http_404(coro):
    """Run a router coroutine; return the HTTPException it must raise."""
    try:
        run(coro)
    except HTTPException as exc:
        assert exc.status_code == 404, f"expected 404, got {exc.status_code}"
        return exc
    raise AssertionError("router did not raise HTTPException")


_SLUG_ROWS = [
    {"slug": "regex-mastery"},
    {"slug": "api-debugging"},
    {"slug": "note-taking-systems"},
]


def _body(slug, version="1.0.0"):
    return InstallIn(slug=slug, version=version, client="test")


def test_unknown_slug_suggests_close_match():
    # get_version -> None (fetchrow), list_version_labels -> [] (fetch),
    # suggest_slugs -> rows (fetch)
    db = ScriptedDB([None, [], _SLUG_ROWS])
    exc = _http_404(ratings_router.record_install(_body("regx-mastery"), db))
    body = exc.detail
    assert body["message"].startswith("no skill 'regx-mastery'")
    assert "regex-mastery" in body["message"]
    assert "regex-mastery" in body["suggestions"]
    assert "available_versions" not in body


def test_unknown_slug_without_suggestion_still_names_the_skill():
    db = ScriptedDB([None, [], [{"slug": "zzz-unrelated"}]])
    exc = _http_404(ratings_router.record_install(_body("qqq-nope"), db))
    body = exc.detail
    assert body["message"] == "no skill 'qqq-nope'"
    assert body["suggestions"] == []


def test_unknown_version_lists_available_versions():
    # get_version -> None, list_version_labels -> two labels; no suggest call.
    db = ScriptedDB(
        [None, [{"version": "1.0.0"}, {"version": "1.1.0"}]]
    )
    exc = _http_404(ratings_router.record_install(_body("regex-mastery", "9.9.9"), db))
    body = exc.detail
    assert body["message"] == (
        "no version '9.9.9' of 'regex-mastery'; "
        "available versions: 1.0.0, 1.1.0"
    )
    assert body["available_versions"] == ["1.0.0", "1.1.0"]
    assert "suggestions" not in body


def test_success_path_records_install_and_returns_ok():
    db = ScriptedDB([{"id": "ver-123", "version": "1.0.0"}])
    out = run(ratings_router.record_install(_body("regex-mastery", "1.0.0"), db))
    assert out == {"ok": True, "slug": "regex-mastery", "version": "1.0.0"}
    assert len(db.executes) == 2  # install_events insert + counter bump
    assert "install_events" in db.executes[0][0]
    assert "downloads = downloads + 1" in db.executes[1][0]
