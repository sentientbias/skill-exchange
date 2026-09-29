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
write endpoints, before auth/deps run. Authenticated writes are budgeted
separately, per account, via api.deps.account_write_budget (see
ACCOUNT_BUCKETS below): a stolen or abused API key gets a budget too, not
just anonymous callers. Moderator decision endpoints stay operator-side by
design. Authenticated *reads* are not budgeted anywhere: they are bearer-key
gated and the expensive ones already fail fast on validation.

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
  expired hits on every check; keys whose newest hit is older than every
  bucket window are swept once the map exceeds _KEY_CAP, so memory stays
  bounded even under a key-flood (threat-8-adjacent resource exhaustion).
- Bucket keys use the matched budget *prefix*, not the raw request path:
  `/api/v1/installs/<anything>` shares the `/api/v1/installs` bucket, so a
  flood of junk path variants cannot mint fresh buckets to dodge the limit
  or grow the map (the middleware runs before route matching, so those
  probes 404 downstream but still reach the limiter).
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

# Per-ACCOUNT budgets on authenticated writes. The middleware above runs
# before auth (it never sees the account), so these live in a FastAPI
# dependency (api.deps.account_write_budget) that runs after
# current_account and calls check_account_budget below.
#
# Why: a stolen or abused API key (threat 6) previously had no rate budget
# at all -- one key could mint keys without limit, spam publishes into the
# moderation queue (reviewer DoS), and machine-gun ratings. The anonymous
# IP limiter does not see these endpoints (they are all authenticated), so
# the blast radius of a compromised key was unbounded. These budgets are
# generous for legitimate human/machine use and bite only scripts.
#
# Key revocation (DELETE /api/v1/accounts/me/keys) is budgeted too: a
# stolen key could otherwise nuke every other key on the account in
# seconds (account lockout / scorched earth) while the victim's recovery
# path -- minting replacement keys -- is itself throttled at 5/60 s.
# Deleting more than a few keys a minute is never legitimate.
#
# (method, path prefix) -> (max hits, window seconds); longest-prefix match.
ACCOUNT_BUCKETS: dict[tuple[str, str], tuple[int, int]] = {
    ("POST", "/api/v1/skills"): (30, 60),
    ("POST", "/api/v1/accounts/me/keys"): (5, 60),
    ("DELETE", "/api/v1/accounts/me/keys"): (5, 60),
}

# monotonic-time hits per "acct METHOD budget-prefix account-id" key.
_account_hits: dict[str, list[float]] = {}

_ACCOUNT_KEY_CAP = 100_000


def _account_matched(method: str, path: str) -> tuple[str, tuple[int, int]] | None:
    """Longest-prefix ACCOUNT_BUCKETS match; returns (prefix, budget)."""
    best: tuple[str, tuple[int, int]] | None = None
    best_len = -1
    for (m, prefix), budget in ACCOUNT_BUCKETS.items():
        if m == method and path.startswith(prefix) and len(prefix) > best_len:
            best, best_len = (prefix, budget), len(prefix)
    return best


def _account_sweep(now: float) -> int:
    """Delete account-bucket keys whose newest hit predates every window."""
    try:
        horizon = max((w for _, w in ACCOUNT_BUCKETS.values()), default=0)
    except Exception:
        return 0
    cutoff = now - horizon
    stale = [k for k, hits in _account_hits.items() if not hits or max(hits) <= cutoff]
    for k in stale:
        del _account_hits[k]
    return len(stale)


def check_account_budget(account_id: str, method: str, path: str) -> float:
    """Charge one hit against the per-account write budget.

    Returns seconds until retry (0 if allowed). Raises nothing; the caller
    (api.deps.account_write_budget) turns a nonzero return into a 429.
    """
    matched = _account_matched(method, path) if path.startswith(_API_PREFIX) else None
    if matched is None:
        return 0.0
    prefix, (max_hits, window) = matched
    now = _monotonic()
    key = f"acct {method} {prefix} {account_id}"
    if len(_account_hits) > _ACCOUNT_KEY_CAP:
        _account_sweep(now)
    return _check(key, max_hits, window, now, hits=_account_hits)


def _reset_account() -> None:
    """Test helper: clear per-account rate-limit state."""
    _account_hits.clear()

# monotonic-time hits per "METHOD budget-prefix client-ip" key.
# The key uses the matched budget prefix rather than the raw request path:
# junk path variants under a budgeted prefix share one bucket instead of
# minting fresh ones (the limiter runs before route matching).
_hits: dict[str, list[float]] = {}

# Once the map grows past this many keys, the next request sweeps keys whose
# newest hit is older than every bucket window. 100k keys is low tens of MB;
# without a cap, a junk-key flood (path variants, IP churn) could grow the
# in-process map without bound on a free-tier box.
_KEY_CAP = 100_000


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


def _matched(method: str, path: str) -> tuple[str, tuple[int, int]] | None:
    """Longest-prefix (method, prefix) match; returns (prefix, budget)."""
    best: tuple[str, tuple[int, int]] | None = None
    best_len = -1
    for (m, prefix), budget in BUCKETS.items():
        if m == method and path.startswith(prefix) and len(prefix) > best_len:
            best, best_len = (prefix, budget), len(prefix)
    return best


def _budget_for(method: str, path: str) -> tuple[int, int] | None:
    """Longest path-prefix match for this method; None if not budgeted."""
    matched = _matched(method, path)
    return matched[1] if matched else None


def _sweep(now: float) -> int:
    """Delete keys whose newest hit predates every bucket window.

    Returns the number of keys removed. Called only when the map exceeds
    _KEY_CAP, so its O(n) scan is amortized across at least _KEY_CAP
    requests. Never raises: a sweep must not break the request path.
    """
    try:
        horizon = max((w for _, w in BUCKETS.values()), default=0)
    except Exception:
        return 0
    cutoff = now - horizon
    stale = [k for k, hits in _hits.items() if not hits or max(hits) <= cutoff]
    for k in stale:
        del _hits[k]
    return len(stale)


def _check(
    key: str, max_hits: int, window: float, now: float,
    *, hits: dict[str, list[float]] | None = None,
) -> float:
    """Record a hit; return seconds until retry (0 if allowed).

    `hits` is the backing map; callers that pass their own map get an
    independent bucket keyspace (the per-account limiter uses its own).
    Defaults to the anonymous limiter's module-level _hits.
    """
    store = _hits if hits is None else hits
    cutoff = now - window
    store_hits = store.get(key)
    if store_hits is None:
        store[key] = [now]
        return 0.0
    # prune expired hits in place (keeps memory bounded by recent traffic)
    fresh = [t for t in store_hits if t > cutoff]
    if len(fresh) >= max_hits:
        oldest = min(fresh)
        store[key] = fresh  # already pruned; keep the window for retry math
        return max(1.0, window - (now - oldest))
    fresh.append(now)
    store[key] = fresh
    return 0.0


def _reset() -> None:
    """Test helper: clear all rate-limit state."""
    _hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        matched = _matched(request.method, path) if path.startswith(_API_PREFIX) else None
        if matched is None:
            return await call_next(request)
        prefix, (max_hits, window) = matched
        now = _monotonic()
        key = f"{request.method} {prefix} {_client_ip(request)}"
        if len(_hits) > _KEY_CAP:
            _sweep(now)
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
