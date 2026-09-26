"""Cache-Control: no-store on credential-bearing responses.

Several endpoints return bearer credentials in the response body:

- POST /api/v1/accounts          -> the new API key, shown ONCE in plaintext
- POST /api/v1/accounts/me/keys  -> a fresh API key, shown once in plaintext
- GET  /api/v1/accounts/me/pro-passes -> pro-pass bearer tokens

Anyone holding one of these values *is* the account for auth purposes, so a
cached copy is a credential leak: a shared proxy, a CDN edge, or the
client's own HTTP cache retaining the response hands the key to whoever can
read the cache. Live probes (2026-09-26) confirmed no Cache-Control header
on any response today, so this was entirely up to intermediary defaults.
Standard practice (OWASP: sensitive response data must not be stored) is
``Cache-Control: no-store`` on such responses.

The rule is deliberately simple and slightly broad: any request that
presented an ``Authorization`` header gets ``no-store`` on its response,
plus the one anonymous credential-issuing route (POST /api/v1/accounts,
the one-time key display). Public catalog JSON stays cacheable, which is
what we want if an edge cache ever fronts the API. Cost is zero: no
legitimate client relies on HTTP caching of authenticated responses here
(the MCP server and install.sh use plain httpx/curl with no cache).

Like the guardrail headers, this is a default, not a mandate: a route that
sets Cache-Control deliberately is never clobbered.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

NO_STORE = "no-store"

# The only anonymous endpoint that returns a credential: account creation
# shows the plaintext API key exactly once. Everything else that returns a
# credential requires the request to carry an Authorization header, which
# the check below covers.
_ANON_CREDENTIAL_ROUTES = {("POST", "/api/v1/accounts")}


class NoStoreOnCredentialsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        credentialed = (
            request.headers.get("authorization")
            or (request.method, request.url.path) in _ANON_CREDENTIAL_ROUTES
        )
        if credentialed:
            response.headers.setdefault("Cache-Control", NO_STORE)
        return response
