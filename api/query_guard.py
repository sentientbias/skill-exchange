"""Reject unknown query parameters on /api/* routes with 422.

FastAPI silently ignores query params that no endpoint declares, so a typo
(?categroy=) or a guessed filter (?pack=paid) returns *unfiltered* data with
a 200 — the caller asked for filtered data and got wrong data. This
middleware fails loudly instead: any query param not declared by the matched
route (including params from sub-dependencies) gets a 422 naming the
offenders.

Route discovery is duck-typed on purpose: recent FastAPI versions defer
include_router() via lazy wrapper objects, so we recurse into anything that
looks like an included router (original_router + include_context.prefix)
instead of assuming app.routes is already flat.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware

_API_PREFIX = "/api/"


def _known_query_params(dependant) -> set[str]:
    names = {p.alias for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _known_query_params(sub)
    return names


def _iter_routes(routes, prefix=""):
    """Yield (path_prefix, route) for APIRoutes, descending into lazily
    included routers (FastAPI >= 0.12x _IncludedRouter)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix, route
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            ctx = getattr(route, "include_context", None)
            sub_prefix = getattr(ctx, "prefix", "") if ctx is not None else ""
            yield from _iter_routes(
                getattr(original, "routes", []), prefix + sub_prefix
            )


def _match_route(app_routes, path: str, method: str):
    """First route matching path+method, mirroring FastAPI routing order."""
    for prefix, route in _iter_routes(app_routes):
        if method not in route.methods:
            continue
        sub_path = path[len(prefix):] if path.startswith(prefix) else None
        if sub_path is None:
            continue
        if route.path_regex.match(sub_path):
            return route
    return None


class RejectUnknownQueryParamsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(_API_PREFIX):
            route = _match_route(request.app.routes, path, request.method)
            if route is not None:
                known = _known_query_params(route.dependant)
                unknown = sorted(
                    {k for k in request.query_params.keys() if k not in known}
                )
                if unknown:
                    return JSONResponse(
                        status_code=422,  # literal: safe across starlette versions
                        content={
                            "detail": (
                                "Unknown query parameter(s): "
                                + ", ".join(unknown)
                                + ". Valid parameters for this endpoint: "
                                + (", ".join(sorted(known)) or "(none)")
                            )
                        },
                    )
        return await call_next(request)
