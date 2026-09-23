"""Security response headers (nosniff, referrer policy, no framing).

Threat under test: the registry serves publisher-influenced bytes to
browsers — HTML pages rendering escaped publisher markdown, raw SKILL.md
as text/markdown inline, and zip bundles. api/security_headers.py stamps
three guardrail headers on every response so a hostile byte sequence
cannot be MIME-sniffed into a document or framed into a clickjacking
overlay.

Run:  pytest tests/test_security_headers.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.responses import JSONResponse, PlainTextResponse

from api.security_headers import HEADERS, SecurityHeadersMiddleware


def _run(coro):
    return asyncio.run(coro)


def _call(path="/", method="GET", status=200, body=b"ok"):
    """Run the middleware against a bare ASGI request, capture the sent
    response (status, headers, body) without a real server."""
    captured = {}

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain")],
        })
        await send({"type": "http.response.body", "body": body})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": [(b"host", b"test")],
    }
    _run(SecurityHeadersMiddleware(app)(scope, receive, send))
    for msg in sent:
        if msg["type"] == "http.response.start":
            captured["status"] = msg["status"]
            captured["headers"] = {
                k.decode(): v.decode() for k, v in msg["headers"]
            }
    return captured


def test_all_three_headers_present():
    headers = _call()["headers"]
    for name, value in HEADERS.items():
        assert headers.get(name.lower()) == value, name


def test_headers_survive_non_200_responses():
    # The middleware is registered outermost so even short-circuited
    # 429/413/422 responses from the guards inside it carry the headers.
    for status in (400, 404, 413, 422, 429, 500):
        headers = _call(status=status)["headers"]
        assert headers.get("x-content-type-options") == "nosniff", status


def test_headers_present_on_json_and_text_responses():
    async def json_app(scope, receive, send):
        resp = JSONResponse({"ok": True})
        await resp(scope, receive, send)

    async def md_app(scope, receive, send):
        resp = PlainTextResponse("hello", media_type="text/markdown")
        await resp(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    for inner in (json_app, md_app):
        sent = []

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "http", "path": "/", "query_string": b"",
            "headers": [(b"host", b"test")],
        }
        _run(SecurityHeadersMiddleware(inner)(scope, receive, send))
        headers = {
            k.decode(): v.decode()
            for m in sent if m["type"] == "http.response.start"
            for k, v in m["headers"]
        }
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "DENY"


def test_route_set_header_is_not_clobbered():
    """Guardrail defaults must not override a deliberate route header."""
    async def app(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"x-frame-options", b"SAMEORIGIN"),
            ],
        })
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/", "query_string": b"",
        "headers": [(b"host", b"test")],
    }
    _run(SecurityHeadersMiddleware(app)(scope, receive, send))
    headers = {
        k.decode(): v.decode()
        for m in sent if m["type"] == "http.response.start"
        for k, v in m["headers"]
    }
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["x-content-type-options"] == "nosniff"
