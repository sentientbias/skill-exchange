"""Total match counts on the paginated catalog listing (pass #8, Functions).

GET /api/v1/skills now returns `total` (page-independent match count,
npm-style) alongside `items`/`limit`/`offset`, and the public MCP
search_skills tool passes it through as `count` instead of reporting the
page size.

Stub-pool style (no DB): assert SQL composition, filter parity between
list and count, router response shape, and the MCP passthrough.

Run:  pytest tests/test_skills_total.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import skills as skills_router
from core import store


class StubDB:
    def __init__(self, rows=None, row=None):
        self.rows = rows or []
        self.row = row or {}
        self.queries = []

    async def fetch(self, query, *params):
        self.queries.append((query, params))
        return self.rows

    async def fetchrow(self, query, *params):
        self.queries.append((query, params))
        return self.row


def run(coro):
    return asyncio.run(coro)


def _row():
    return {
        "id": "x", "slug": "s", "name": "n", "description": "d",
        "category": "devtools", "status": "approved",
        "created_at": None, "updated_at": None, "publisher": "mikey",
        "latest_version": "1.0.0", "avg_stars": 0.0, "rating_count": 0,
        "downloads": 0, "signed": True,
    }


def _where(query):
    """Extract the WHERE-clause text from a composed query for comparison."""
    tail = query.split("where (", 1)[1]
    return tail.split(" order by ")[0].rstrip()


# --- count_skills --------------------------------------------------------

def test_count_returns_total_int():
    db = StubDB(row={"total": 74})
    assert run(store.count_skills(db)) == 74


def test_count_query_has_no_limit_or_offset():
    db = StubDB(row={"total": 3})
    run(store.count_skills(db))
    query, _ = db.queries[0]
    assert "count(*)::int as total" in query
    assert "limit" not in query.lower()
    assert "offset" not in query.lower()


def test_count_and_list_where_clauses_agree():
    """Same filters -> same WHERE text: page and total can never disagree."""
    filters = dict(q="regex", category="devtools",
                   since="2026-09-01T00:00:00Z")
    db_list = StubDB(rows=[_row()])
    db_count = StubDB(row={"total": 2})
    run(store.list_skills(db_list, **filters))
    run(store.count_skills(db_count, **filters))
    assert _where(db_list.queries[0][0]) == _where(db_count.queries[0][0])


def test_count_and_list_params_agree_on_filters():
    filters = dict(q="regex", category="devtools",
                   since="2026-09-01T00:00:00Z")
    db_list = StubDB(rows=[_row()])
    db_count = StubDB(row={"total": 2})
    run(store.list_skills(db_list, **filters))
    run(store.count_skills(db_count, **filters))
    # count params are the filter params (q, category, since); the list
    # appends limit/offset after them.
    count_params = db_count.queries[0][1]
    list_params = db_list.queries[0][1]
    assert count_params == ("regex", "devtools", "2026-09-01T00:00:00Z")
    assert list_params[:3] == count_params


def test_count_respects_status_filter():
    db = StubDB(row={"total": 1})
    run(store.count_skills(db))
    assert "s.status = 'approved'" in db.queries[0][0]
    db2 = StubDB(row={"total": 2})
    run(store.count_skills(db2, include_pending=True))
    assert "s.status = 'approved'" not in db2.queries[0][0]


# --- router response shape -----------------------------------------------

def test_list_route_returns_total_with_backward_compat_keys():
    db = StubDB(rows=[_row(), _row()], row={"total": 74})
    resp = run(skills_router.list_skills(
        q="", category="", sort="newest", limit=10, offset=5,
        since="", pool=db))
    assert resp["total"] == 74
    assert len(resp["items"]) == 2
    assert resp["limit"] == 10
    assert resp["offset"] == 5


def test_list_route_passes_filters_to_count():
    db = StubDB(rows=[], row={"total": 0})
    run(skills_router.list_skills(
        q="video", category="media", sort="newest", limit=20, offset=0,
        since="2026-09-20T00:00:00Z", pool=db))
    count_query, count_params = db.queries[1]  # second call is the count
    assert "count(*)::int as total" in count_query
    assert "video" in count_params and "media" in count_params
    assert "2026-09-20T00:00:00Z" in count_params


# --- public MCP server passthrough ---------------------------------------

def _patch_http(monkey_value):
    import mcp_server.public_server as ps

    real = ps._http_get_json

    def fake(path, params):
        return monkey_value
    ps._http_get_json = fake
    return ps, real


def test_mcp_search_skills_count_is_catalog_total():
    import mcp_server.public_server as ps
    ps, real = _patch_http({
        "items": [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}],
        "limit": 3, "offset": 0, "total": 42,
    })
    try:
        got = ps.search_skills(query="agent")
    finally:
        ps._http_get_json = real
    assert got["count"] == 42  # not len(items) == 3
    assert len(got["skills"]) == 3


def test_mcp_search_skills_count_falls_back_without_total():
    import mcp_server.public_server as ps
    ps, real = _patch_http({"items": [{"slug": "a"}]})  # older API build
    try:
        got = ps.search_skills(query="agent")
    finally:
        ps._http_get_json = real
    assert got["count"] == 1
