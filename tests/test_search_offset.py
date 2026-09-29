"""Offset paging on the MCP search_skills tools (both servers).

The API has always supported offset (0-10000) on GET /api/v1/skills, and the
catalog is past 180 skills, but both MCP search_skills tools hardcoded
offset=0 -- an agent asking for "more devtools skills" or "skills 51-100"
hit a wall at the first page. The tool also reports the page-independent
`count`, which is only useful if the caller can actually page.

This adds an `offset` parameter to both tools, with the same validation the
list_bundles tool already uses: int coercion, clamped to 0..10000 (the
API's deep-offset guard). No page, copy, API shape, Pro-tier, or behavior
change beyond the new parameter.

Run:  pytest tests/test_search_offset.py   (no database needed)
"""
import asyncio
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import mcp_server.public_server as ps
import mcp_server.server as mcpd
from mcp_server.public_server import PlaybookError


def _search_with_params(offset):
    """Call the public search_skills with a stubbed HTTP layer; return the
    params dict the tool sent to the API."""
    captured = {}

    def fake(path, params=None):
        captured["path"] = path
        captured["params"] = dict(params or {})
        return {"items": [{"slug": "a"}], "total": 187}

    with mock.patch.object(ps, "_http_get_json", side_effect=fake):
        out = ps.search_skills("devtools", offset=offset)
    assert out["count"] == 187
    assert captured["path"] == "/api/v1/skills"
    return captured["params"]


def test_public_search_offset_defaults_to_zero():
    captured = {}

    def fake(path, params=None):
        captured["params"] = dict(params or {})
        return {"items": [], "total": 0}

    with mock.patch.object(ps, "_http_get_json", side_effect=fake):
        ps.search_skills("devtools")
    assert captured["params"]["offset"] == 0


def test_public_search_offset_passed_through():
    assert _search_with_params(40)["offset"] == 40


def test_public_search_offset_string_coerced():
    assert _search_with_params("25")["offset"] == 25


def test_public_search_offset_negative_clamps_to_zero():
    assert _search_with_params(-5)["offset"] == 0


def test_public_search_offset_huge_clamps_to_api_cap():
    assert _search_with_params(10 ** 9)["offset"] == 10000


def test_public_search_offset_garbage_raises():
    with pytest.raises(PlaybookError, match="Invalid offset"):
        _search_with_params("abc")


# ---------------------------------------------------------------------------
# The operator-side (authenticated) server calls store.list_skills directly,
# so patch the pool and the store call and watch the kwargs.
# ---------------------------------------------------------------------------

def _run_authed_search(offset):
    captured = {}

    async def fake_pool():
        return object()

    async def fake_list_skills(pool, **kwargs):
        captured.update(kwargs)
        return []

    with mock.patch.object(mcpd.db, "get_pool", side_effect=fake_pool), \
         mock.patch.object(mcpd.store, "list_skills",
                           side_effect=fake_list_skills):
        out = asyncio.run(mcpd.search_skills("devtools", offset=offset))
    assert out == {"skills": []}
    return captured


def test_authed_search_offset_passed_through():
    assert _run_authed_search(30)["offset"] == 30


def test_authed_search_offset_defaults_to_zero():
    captured = {}

    async def fake_pool():
        return object()

    async def fake_list_skills(pool, **kwargs):
        captured.update(kwargs)
        return []

    with mock.patch.object(mcpd.db, "get_pool", side_effect=fake_pool), \
         mock.patch.object(mcpd.store, "list_skills",
                           side_effect=fake_list_skills):
        asyncio.run(mcpd.search_skills("devtools"))
    assert captured["offset"] == 0


def test_authed_search_offset_clamped_both_ends():
    assert _run_authed_search(-2)["offset"] == 0
    assert _run_authed_search(10 ** 9)["offset"] == 10000


def test_authed_search_offset_garbage_returns_error():
    out = asyncio.run(mcpd.search_skills("devtools", offset="nope"))
    assert "error" in out and "offset" in out["error"]
