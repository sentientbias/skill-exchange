"""Per-IP rate limiting on anonymous /api/* write endpoints (HTTP 429).

Threat: several write endpoints are reachable without any account — anonymous
account signup (POST /accounts) and anonymous install logging (POST /installs).
With no throttle, a single script can:

- mint unlimited accounts (Sybil fuel for rating manipulation, threat #5), and
- forge install events at will, inflating the `downloads` counts shown on the
  front door and per-skill detail pages. PyPI learned this the hard way: the
  Playbook computes downloads from a client-callable counter, so the count is
  only as honest as the cheapest writer.

This middleware applies a sliding-window budget per client IP to the anonymous
write endpoints, before auth/deps run. Authenticated endpoints are not
budgeted here: publish/rate calls are already gated by API keys + signatures,
and per-account abuse there is an operator problem, not a free-for-all.

Behavior:
- Budgets are keyed (HTTP method, path prefix), longest-prefix match:
    POST /api/v1/installs -> 30 hits / 60 s per IP  (installers log rarely;
    30/min is generous for one machine)
    POST /api/v1/accounts  -> 10 hits / 60 s per IP  (human-speed signup;
    still roomy for a classroom behind one NAT)
  Every other /api/* route is untouched.
- Over budget -> 429 JSON + Retry-After header (seconds until the oldest
  hit in the window expires).
- Client IP is the RIGHTMOST X-Forwarded-For entry when present, else
  request.client.host. Proxies append the peer they observed to the right
  of the header; everything to the left is client-controlled. Render is the
  one trusted terminating proxy and appends its own observation, so the
  rightmost entry is the only one the client could not write -- keying on
  the leftmost (the intuitive reading) let an attacker mint a fresh bucket
  per request with a rotated forged prefix, bypassing the limit entirely.
  Honest caveat: per-IP buckets still fall to an adversary with many real
  egress IPs, and shared-NAT clients share a budget. This reading is only
  sound while exactly one trusted proxy appends: if the proxy topology
  changes, revisit. The 1 MiB body guard (request_size_guard.py) sits in
  front of this; order in main.py is body guard first.
- State is in-process (free-tier boxes run one worker). Buckets prune their
  own expired hits on every check, so memory is bounded by recent traffic.
"""
from __future__ import annotations

from time import monotonic as _time_monotonic

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Module-level alias so tests can patch the clock (patching time.monotonic
# itself would break asyncio's internals). Production always uses the real
# monotonic clock.
_monotonic = _time_monotonic

_API_PREFIX = "/api/"

# (method, path prefix) -> (max hits, window seconds)
BUCKETS: dict[tuple[str, str], tuple[int, int]] = {
    ("POST", "/api/v1/installs"): (30, 60),
    ("POST", "/api/v1/accounts"): (10, 60),
}

# monotonic-time hits per "METHOD path-prefix client-ip" key
_hits: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    # Rightmost XFF entry: proxies append the peer they observed to the
    # right, so the rightmost hop is the one the client could not write --
    # Render (our trusted terminating proxy) appends the real client IP.
    # Everything left of it is attacker-controlled and ignored. Without XFF,
    # fall back to the direct peer (the proxy itself, in production).
    xff = request.headers.get("x-forwarded-for")
    if xff:
        entries = [e.strip() for e in xff.split(",")]
        for entry in reversed(entries):
            if entry:
                return entry
    return request.client.host if request.client else "unknown"


def _budget_for(method: str, path: str) -> tuple[int, int] | None:
    """Longest path-prefix match for this method; None if not budgeted."""
    best: tuple[int, int] | None = None
    best_len = -1
    for (m, prefix), budget in BUCKETS.items():
        if m == method and path.startswith(prefix) and len(prefix) > best_len:
            best, best_len = budget, len(prefix)
    return best


def _check(key: str, max_hits: int, window: float, now: float) -> float:
    """Record a hit; return seconds until retry (0 if allowed)."""
    cutoff = now - window
    hits = _hits.get(key)
    if hits is None:
        _hits[key] = [now]
        return 0.0
    # prune expired hits in place (keeps memory bounded by recent traffic)
    fresh = [t for t in hits if t > cutoff]
    if len(fresh) >= max_hits:
        oldest = min(fresh)
        _hits[key] = fresh  # already pruned; keep the window for retry math
        return max(1.0, window - (now - oldest))
    fresh.append(now)
    _hits[key] = fresh
    return 0.0


def _reset() -> None:
    """Test helper: clear all rate-limit state."""
    _hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        budget = (
            _budget_for(request.method, path)
            if path.startswith(_API_PREFIX)
            else None
        )
        if budget is None:
            return await call_next(request)
        max_hits, window = budget
        now = _monotonic()
        key = f"{request.method} {path} {_client_ip(request)}"
        retry_after = _check(key, max_hits, window, now)
        if retry_after > 0:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(int(retry_after))},
                content={
                    "detail": (
                        f"Rate limit exceeded: at most {max_hits} requests "
                        f"per {int(window)} seconds. "
                        f"Retry in {int(retry_after)} seconds."
                    )
                },
            )
        return await call_next(request)
