"""Cache-Control: no-store on credential-bearing responses.

Threat under test: several endpoints return bearer credentials in the
response body (the API key shown once at account creation and key rotation,
pro-pass bearer tokens). Without Cache-Control: no-store, a shared proxy,
CDN edge, or client HTTP cache could retain the response and hand the
credential to whoever reads the cache. api/no_store.py stamps no-store on
any response to a request that presented an Authorization header, plus the
one anonymous credential-issuing route (POST /api/v1/accounts). Public
catalog responses stay cacheable.

Run:  pytest tests/test_no_store.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.no_store import NO_STORE, NoStoreOnCredentialsMiddleware


def _run(coro):
    return asyncio.run(coro)


def _call(path="/", method="GET", headers=(), status=200, body=b"ok",
          resp_headers=()):
    """Run the middleware against a bare ASGI request, capture the sent
    response headers without a real server."""
    captured = {}

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")] + list(resp_headers),
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
        "headers": [(b"host", b"test")] + list(headers),
    }
    _run(NoStoreOnCredentialsMiddleware(app)(scope, receive, send))
    for msg in sent:
        if msg["type"] == "http.response.start":
            captured["status"] = msg["status"]
            captured["headers"] = {
                k.decode(): v.decode() for k, v in msg["headers"]
            }
    return captured


def _auth():
    return [(b"authorization", b"Bearer skx_testkey")]


def test_authenticated_request_gets_no_store():
    # Covers key rotation (POST /accounts/me/keys), pro passes
    # (GET /accounts/me/pro-passes), and every other authed endpoint.
    for path, method in (("/api/v1/accounts/me/pro-passes", "GET"),
                         ("/api/v1/accounts/me/keys", "POST"),
                         ("/api/v1/skills", "GET")):
        headers = _call(path, method, headers=_auth())["headers"]
        assert headers.get("cache-control") == NO_STORE, (path, method)


def test_anonymous_public_request_stays_cacheable():
    # Public catalog JSON must NOT be marked no-store: a future edge cache
    # should be able to hold it.
    for path, method in (("/api/v1/skills", "GET"),
                         ("/api/v1/bundles", "GET"),
                         ("/", "GET")):
        headers = _call(path, method)["headers"]
        assert "cache-control" not in headers, (path, method)


def test_anonymous_account_creation_gets_no_store():
    # POST /api/v1/accounts returns the plaintext API key exactly once and
    # carries no Authorization header, so it needs the explicit route rule.
    headers = _call("/api/v1/accounts", "POST")["headers"]
    assert headers.get("cache-control") == NO_STORE


def test_anonymous_non_credential_post_stays_cacheable():
    # Anonymous install logging returns no credential: no header.
    headers = _call("/api/v1/installs", "POST")["headers"]
    assert "cache-control" not in headers


def test_route_set_cache_control_is_not_clobbered():
    # A deliberate route header wins over the guardrail default.
    headers = _call(
        "/api/v1/accounts/me", "GET", headers=_auth(),
        resp_headers=[(b"cache-control", b"public, max-age=60")],
    )["headers"]
    assert headers["cache-control"] == "public, max-age=60"


def test_no_store_survives_error_responses():
    # An authed request that 401s/422s still carried (or would carry) the
    # credential in the request; the response gets no-store too.
    for status in (401, 403, 422):
        headers = _call("/api/v1/accounts/me", "GET", headers=_auth(),
                        status=status)["headers"]
        assert headers.get("cache-control") == NO_STORE, status
