"""Security response headers (defense in depth for browser clients).

The registry serves publisher-influenced bytes to browsers on several
surfaces:

- GET /browse, /skill pages, front door — HTML that renders escaped
  publisher markdown (names, descriptions, install commands) inline.
- GET /api/v1/skills/{slug}/skill.md — the publisher's raw SKILL.md served
  ``inline`` as ``text/markdown; charset=utf-8``.
- GET /api/v1/bundles/{slug} — a zip of publisher content.

This middleware stamps three cheap, reversible headers on every response
(including short-circuited 413/429/422/404s — it is registered outermost so
nothing escapes it):

- ``X-Content-Type-Options: nosniff`` — the sharp one. Without it, a
  browser may MIME-sniff a hostile SKILL.md (or a bundle/zip filename edge)
  and reinterpret declared text as HTML, which is the classic route from
  "publisher controlled bytes" to script execution in a visitor's browser.
  With nosniff the browser honors the declared type, and every declared
  type we serve is inert as text (markdown served as text/markdown renders
  as text, never as a document).
- ``Referrer-Policy: strict-origin-when-cross-origin`` — registry pages link
  out to publisher-supplied URLs; full request URLs (slugs, params) must not
  leak to third parties via the Referer header.
- ``X-Frame-Options: DENY`` — the pages carry trust cues ("signature
  verified", download buttons). They must not be frameable, which is what
  clickjacking overlays abuse.

Why this is safe to apply globally: auth is bearer-header only (no
cookies), so there is nothing for a framed or cross-origin context to
steal; CORS is unchanged (``*`` on a cookieless public API, as npm/PyPI do);
static brand assets keep their declared image/svg types and render fine.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for name, value in HEADERS.items():
            # Never clobber a header a route set deliberately; these are
            # guardrail defaults, not a mandate.
            response.headers.setdefault(name, value)
        return response
