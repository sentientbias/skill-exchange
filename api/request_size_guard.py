"""Cap inbound request body sizes on /api/* write endpoints (HTTP 413).

Threat: several write endpoints are open or cheaply reachable — anonymous
account creation (POST /accounts), anonymous install logging
(POST /installs), and any authenticated publish/rate call. Request bodies
were unbounded: FastAPI parses the *entire* JSON body into memory before any
store-layer check (e.g. the 200k-char skill_md cap) runs, so a single huge
POST could spike memory on a free-tier box. This middleware rejects
oversized bodies *before* they are parsed.

Behavior:
- Applies only to /api/* routes with a write method (POST/PUT/PATCH/DELETE).
  GETs and the front door (/) are untouched.
- If Content-Length exceeds the cap -> 413 immediately, body never read.
- If Content-Length is absent (chunked) or unparsable -> the body is read
  incrementally and cut off at cap+1 -> 413 if exceeded. A body that fits
  is re-injected so downstream handlers see it untouched.
- Cap defaults to 1 MiB (the largest legit payload, a 200k-char SKILL.md
  publish, is ~250 KB of JSON — 4x headroom) and is overridable with the
  MAX_REQUEST_BODY_BYTES env var.
"""
from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_API_PREFIX = "/api/"
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_MAX_BODY_BYTES = 1_048_576  # 1 MiB


def _max_body_bytes() -> int:
    try:
        return int(os.environ.get("MAX_REQUEST_BODY_BYTES", "")
                   or DEFAULT_MAX_BODY_BYTES)
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES


MAX_BODY_BYTES = _max_body_bytes()


async def _body_too_big(request: Request, limit: int) -> tuple[bool, bytes]:
    """Read the request body up to limit+1 bytes.

    Returns (too_big, body). A body that fits is returned whole so the caller
    can re-inject it for downstream handlers.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            return True, b""
    return False, b"".join(chunks)


class RequestSizeGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(_API_PREFIX) and request.method in _WRITE_METHODS:
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) > MAX_BODY_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "detail": (
                                    "Request body too large: "
                                    f"Content-Length {declared} exceeds the "
                                    f"{MAX_BODY_BYTES}-byte limit."
                                )
                            },
                        )
                except ValueError:
                    pass  # unparsable header -> fall through to streamed read
            too_big, body = await _body_too_big(request, MAX_BODY_BYTES)
            if too_big:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            "Request body too large: exceeds the "
                            f"{MAX_BODY_BYTES}-byte limit."
                        )
                    },
                )
            # Re-inject so downstream request.body()/request.json() still work.
            request._body = body  # noqa: SLF001 (Starlette's documented cache)
        return await call_next(request)
