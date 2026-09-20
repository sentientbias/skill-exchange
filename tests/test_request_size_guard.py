"""Request body size guard: oversized /api/* write bodies get a 413 before
FastAPI parses them into memory.

Design under test: threat #8 in SECURITY.md — open or cheaply-reachable
write endpoints (anonymous signup, anonymous install logging, authenticated
publishes) accepted unbounded bodies; the store-layer 200k-char skill_md
check only runs *after* the full JSON body is parsed. The middleware caps
bodies at 1 MiB (Content-Length short-circuit + streamed read for chunked
bodies), and api/schemas.py caps individual fields for precise 422s.

Run:  pytest tests/test_request_size_guard.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pydantic import ValidationError

from api.request_size_guard import (
    RequestSizeGuardMiddleware,
    _body_too_big,  # noqa: F401 (kept importable for future tests)
)
import api.request_size_guard as rsg


def _run(coro):
    return asyncio.run(coro)


def _scope(method, path, content_length=None, chunked=False):
    headers = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    if chunked:
        headers.append((b"transfer-encoding", b"chunked"))
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": ("test", 1234),
    }


def _receive_factory(chunks):
    msgs = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]

    async def receive():
        if msgs:
            return msgs.pop(0)
        # a real server ends the body with an empty http.request message
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


async def _echo_next(request):
    """Downstream handler: returns the body it received (proves re-injection)."""
    from fastapi.responses import JSONResponse

    body = await request.body()
    return JSONResponse({"len": len(body), "prefix": body[:8].decode("utf-8", "replace")})


def _dispatch(method, path, chunks, content_length=None, cap=64, chunked=False):
    """Drive the middleware with a stubbed ASGI receive; return (response, next_called)."""
    from fastapi import Request

    called = []

    async def spy_next(request):
        called.append(True)
        return await _echo_next(request)

    monkey_cap = rsg.MAX_BODY_BYTES
    rsg.MAX_BODY_BYTES = cap
    try:
        mw = RequestSizeGuardMiddleware(app=None)
        request = Request(
            _scope(method, path, content_length, chunked),
            receive=_receive_factory(chunks),
        )
        resp = _run(mw.dispatch(request, spy_next))
    finally:
        rsg.MAX_BODY_BYTES = monkey_cap
    return resp, called


# ---------------------------------------------------------------------------
# middleware
# ---------------------------------------------------------------------------

def test_declared_content_length_over_cap_is_413_and_never_read():
    resp, called = _dispatch("POST", "/api/v1/skills", [b"x" * 10],
                            content_length=1000, cap=64)
    assert resp.status_code == 413
    assert not called  # body never touched downstream
    body = resp.body
    assert b"too large" in body


def test_body_under_cap_passes_and_is_reinjected_intact():
    payload = b'{"hello": "world"}'
    resp, called = _dispatch("POST", "/api/v1/skills", [payload],
                            content_length=len(payload), cap=64)
    assert resp.status_code == 200
    assert called
    import json

    data = json.loads(resp.body)
    assert data["len"] == len(payload)
    assert data["prefix"] == '{"hello"'


def test_chunked_body_over_cap_is_413():
    chunks = [b"y" * 40, b"y" * 40]  # 80 > 64, no Content-Length
    resp, called = _dispatch("POST", "/api/v1/installs", chunks,
                            content_length=None, cap=64, chunked=True)
    assert resp.status_code == 413
    assert not called


def test_chunked_body_under_cap_passes():
    payload = b"z" * 60
    resp, called = _dispatch("POST", "/api/v1/installs", [payload[:30], payload[30:]],
                            content_length=None, cap=64, chunked=True)
    assert resp.status_code == 200
    assert called


def test_get_with_huge_content_length_is_not_guarded():
    resp, called = _dispatch("GET", "/api/v1/skills", [],
                            content_length=10**9, cap=64)
    assert resp.status_code == 200
    assert called


def test_non_api_path_is_not_guarded():
    resp, called = _dispatch("POST", "/", [b"q" * 10],
                            content_length=10**9, cap=64)
    assert resp.status_code == 200
    assert called


def test_unparsable_content_length_falls_back_to_streamed_read():
    payload = b"ok"
    resp, called = _dispatch("POST", "/api/v1/skills", [payload],
                            content_length="not-a-number", cap=64)
    assert resp.status_code == 200
    assert called


# ---------------------------------------------------------------------------
# schema-level caps (precise 422s at the API boundary)
# ---------------------------------------------------------------------------

def _valid_publish(**over):
    from api.schemas import SkillPublish

    base = dict(
        name="N",
        slug="s",
        description="d",
        skill_md="x" * 100,
        signature="s" * 88,
        public_key="a" * 64,
    )
    base.update(over)
    return SkillPublish(**base)


def test_skill_md_over_store_ceiling_rejected_at_schema():
    with pytest.raises(ValidationError):
        _valid_publish(skill_md="x" * 200_001)


def test_skill_md_at_store_ceiling_accepted():
    _valid_publish(skill_md="x" * 200_000)


def test_garbage_signature_rejected_at_schema():
    with pytest.raises(ValidationError):
        _valid_publish(signature="x" * 10_000)


def test_garbage_public_key_rejected_at_schema():
    with pytest.raises(ValidationError):
        _valid_publish(public_key="x" * 10_000)


def test_rating_comment_cap():
    from api.schemas import RatingIn

    with pytest.raises(ValidationError):
        RatingIn(stars=5, comment="c" * 5001)
    RatingIn(stars=5, comment="c" * 5000)


def test_install_client_cap():
    from api.schemas import InstallIn

    with pytest.raises(ValidationError):
        InstallIn(slug="s", client="c" * 101)
    InstallIn(slug="s", client="c" * 100)


def test_account_signup_text_caps():
    from api.schemas import AccountCreate

    with pytest.raises(ValidationError):
        AccountCreate(handle="h", display_name="d" * 201)
    AccountCreate(handle="h", display_name="d" * 200)
