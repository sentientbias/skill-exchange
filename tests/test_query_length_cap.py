"""Search-text length guard on the public list endpoints (threat 8-adjacent).

GET /api/v1/skills and GET /api/v1/bundles are unauthenticated reads with
no rate budget. The `q` filter feeds three leading-wildcard ILIKE matches
(`slug`, `name`, `description`) per row, so an unbounded `q` let a single
client turn a cheap list read into an expensive full-table pattern scan:
a 4000-char `q` returned 200 on the live API, confirmed 2026-09-27. The
`category` param reaches the same query path and is likewise bounded.
The /browse HTML page already truncates q to 100 chars server-side, so the
API-side caps (200 for q, 64 for category) are consistent with existing
convention. Oversized values now fail fast with a 422 at the FastAPI
validation layer, before any database work.

These tests introspect the declared Query constraints on the real routers
— no database needed, so the caps can't silently regress.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.routing import APIRoute


def _max_lengths(router, path):
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path:
            bounds = {}
            for param in route.dependant.query_params:
                for meta in param.field_info.metadata:
                    if type(meta).__name__ == "MaxLen":
                        bounds[param.name] = meta.max_length
            return bounds
    raise AssertionError(f"no query params found on {path!r}")


def test_skills_list_search_text_is_bounded():
    from api.routers import skills

    bounds = _max_lengths(skills.router, "/skills")
    assert bounds.get("q") == 200, (
        "q max_length missing or changed — unbounded search text is "
        "unauthenticated read amplification (see module docstring)"
    )
    assert bounds.get("category") == 64, (
        "category max_length missing or changed — same query path as q"
    )


def test_bundles_list_search_text_is_bounded():
    from api.routers import bundles

    bounds = _max_lengths(bundles.router, "/bundles")
    assert bounds.get("q") == 200, (
        "q max_length missing or changed — unbounded search text is "
        "unauthenticated read amplification (see module docstring)"
    )
    assert bounds.get("category") == 64, (
        "category max_length missing or changed — same query path as q"
    )
