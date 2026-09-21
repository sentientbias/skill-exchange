"""God-tier API additions: since filter, signed flag, public stats, installer.

Pure-function style (no DB): a stub pool records the SQL it was given so
we can assert on param numbering and filter composition.

Run:  pytest tests/test_god_tier_api.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException

from api.routers.skills import _validate_since
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


# --- since validation -------------------------------------------------------

def test_since_accepts_iso():
    assert _validate_since("2026-09-14T00:00:00Z") == "2026-09-14T00:00:00Z"
    assert _validate_since("2026-09-14T00:00:00+00:00")
    assert _validate_since("2026-09-14")


def test_since_rejects_garbage_with_422():
    for bad in ["yesterday", "2026-13-99", "'; DROP TABLE skills;--", ""]:
        try:
            _validate_since(bad)
        except HTTPException as e:
            assert e.status_code == 422, bad
        else:
            raise AssertionError(f"no 422 for {bad!r}")


# --- list_skills query composition ------------------------------------------

def _row():
    return {
        "id": "x", "slug": "s", "name": "n", "description": "d",
        "category": "devtools", "status": "approved",
        "created_at": None, "updated_at": None, "publisher": "mikey",
        "latest_version": "1.0.0", "avg_stars": 0.0, "rating_count": 0,
        "downloads": 0, "signed": True,
    }


def test_list_select_includes_signed_and_publisher():
    db = StubDB(rows=[_row()])
    items = run(store.list_skills(db))
    assert items[0]["signed"] is True
    assert items[0]["publisher"] == "mikey"
    query = db.queries[0][0]
    assert "as signed" in query
    assert "a.handle as publisher" in query


def test_since_filter_composes_with_correct_param_numbers():
    db = StubDB(rows=[_row()])
    run(store.list_skills(db, q="regex", sort="newest",
                          since="2026-09-14T00:00:00Z"))
    query, params = db.queries[0]
    assert "and s.updated_at > $3::timestamptz" in query, query
    assert params[0] == "regex" and params[1] == ""
    # Regression: the since param must be a tz-aware datetime, not the raw
    # string -- asyncpg rejects strings for $n::timestamptz and the live
    # API 500d on ?since= (2026-09-21).
    assert isinstance(params[2], datetime), params
    assert params[2] == datetime(2026, 9, 14, tzinfo=timezone.utc), params
    assert params[3:] == (20, 0), params


def test_since_coerces_z_and_date_only_to_utc_aware():
    assert store._coerce_since("2026-09-14T00:00:00Z") == datetime(
        2026, 9, 14, tzinfo=timezone.utc)
    assert store._coerce_since("2026-09-14") == datetime(
        2026, 9, 14, tzinfo=timezone.utc)
    passthrough = datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
    assert store._coerce_since(passthrough) is passthrough


def test_no_since_filter_when_empty():
    db = StubDB(rows=[_row()])
    run(store.list_skills(db))
    query, params = db.queries[0]
    assert "updated_at >" not in query
    assert params == ("", "", 20, 0), params


def test_since_does_not_leak_into_include_pending_paths():
    db = StubDB(rows=[_row()])
    run(store.list_skills(db, since="2026-01-01T00:00:00Z",
                          include_pending=True))
    query, _ = db.queries[0]
    assert "s.status = 'approved'" not in query
    assert "updated_at > $3" in query


# --- public_stats ------------------------------------------------------------

def test_public_stats_shape():
    db = StubDB(
        row={"skill_count": 64, "publisher_count": 9, "total_downloads": 20},
        rows=[
            {"category": "devtools", "count": 40},
            {"category": "media", "count": 24},
        ],
    )
    got = run(store.public_stats(db))
    assert got == {
        "skill_count": 64,
        "publisher_count": 9,
        "total_downloads": 20,
        "categories": [
            {"category": "devtools", "count": 40},
            {"category": "media", "count": 24},
        ],
    }


def test_public_stats_counts_only_approved():
    db = StubDB(row={"skill_count": 1, "publisher_count": 1,
                     "total_downloads": 0})
    run(store.public_stats(db))
    for query, _ in db.queries:
        assert "s.status = 'approved'" in query
