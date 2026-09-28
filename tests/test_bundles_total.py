"""Page-independent `total` on GET /api/v1/bundles.

/api/v1/skills already returns a page-independent `total`; /bundles did
not, so an agent paging through a bundle update window with limit/offset
could not tell whether it had seen everything. The fix adds the same
count via store.count_skills with the same filters.

Stub-DB introspection style — no live Postgres needed.
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
    base_url = "https://example.com/"


def run(coro):
    return asyncio.run(coro)


def _run_list_bundles(monkeypatch, **kwargs):
    rows = [{"slug": "regex-mastery", "name": "Regex Mastery",
             "latest_version": "1.0.0"}]
    seen = {}

    async def fake_list_skills(db, **kw):
        seen["list"] = kw
        return rows

    async def fake_count_skills(db, **kw):
        seen["count"] = kw
        return 7

    monkeypatch.setattr(store, "list_skills", fake_list_skills)
    monkeypatch.setattr(store, "count_skills", fake_count_skills)
    call_kwargs = dict(request=StubRequest(), q="", category="", limit=20,
                       offset=0, since="", pool=StubDB())
    call_kwargs.update(kwargs)
    result = run(bundles.list_bundles(**call_kwargs))
    return result, seen


def test_bundles_response_includes_total(monkeypatch):
    result, _ = _run_list_bundles(monkeypatch)
    assert result["total"] == 7, f"total missing/wrong: {result!r}"


def test_bundles_total_uses_the_same_filters(monkeypatch):
    # The count must describe the same query the list did, or paging lies.
    _, seen = _run_list_bundles(monkeypatch, q="re", category="devtools",
                                since="2026-09-01T00:00:00Z")
    count_kw = seen["count"]
    assert count_kw.get("q") == "re", count_kw
    assert count_kw.get("category") == "devtools", count_kw
    assert count_kw.get("since") == "2026-09-01T00:00:00Z", count_kw


def test_bundles_total_still_rejects_garbage_since_before_db(monkeypatch):
    # The since gate must still fire before ANY db call, including count.
    called = {"hit": False}

    async def fake_count_skills(db, **kwargs):
        called["hit"] = True
        return 0

    async def fake_list_skills(db, **kwargs):
        return []

    monkeypatch.setattr(store, "list_skills", fake_list_skills)
    monkeypatch.setattr(store, "count_skills", fake_count_skills)
    try:
        run(bundles.list_bundles(
            request=StubRequest(), q="", category="", limit=20, offset=0,
            since="yesterday", pool=StubDB(),
        ))
    except Exception as e:  # noqa: BLE001 - HTTPException is FastAPI-land
        assert getattr(e, "status_code", None) == 422, e
    else:
        raise AssertionError("no 422 for garbage since on /bundles")
    assert not called["hit"], "count_skills was hit before the since gate"
