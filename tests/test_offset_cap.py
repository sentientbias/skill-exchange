"""Deep-offset guard on the public list endpoints (threat 8-adjacent).

GET /api/v1/skills and GET /api/v1/bundles are unauthenticated reads with
no rate budget. With an unbounded `offset`, Postgres must scan and discard
N rows before returning anything, so a single client can turn a cheap list
read into a full-table scan probe (offset=999999999 returned 200/empty
on the live API, confirmed 2026-09-24). The fix caps offset at 10,000:
no legitimate client pages that deep (the /browse UI clamps page to the
real page count), and anything deeper now fails fast with a 422.

These tests introspect the declared Query constraints on the real routers
— no database needed, so the cap can't silently regress.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Query
from fastapi.routing import APIRoute


def _offset_bounds(router, path):
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path:
            for param in route.dependant.query_params:
                if param.name == "offset":
                    bounds = {}
                    for meta in param.field_info.metadata:
                        if type(meta).__name__ == "Ge":
                            bounds["ge"] = meta.ge
                        elif type(meta).__name__ == "Le":
                            bounds["le"] = meta.le
                    return bounds
    raise AssertionError(f"no offset query param found on {path!r}")


def test_skills_list_offset_is_capped():
    from api.routers import skills

    bounds = _offset_bounds(skills.router, "/skills")
    # Query constraints land on the field info (Pydantic v2: QueryInfo)
    assert bounds.get("ge") == 0, "offset must stay non-negative"
    assert bounds.get("le") == 10000, (
        "offset cap missing or changed — unbounded deep-offset scans are "
        "unauthenticated read amplification (see module docstring)"
    )


def test_bundles_list_offset_is_capped():
    from api.routers import bundles

    bounds = _offset_bounds(bundles.router, "/bundles")
    assert bounds.get("ge") == 0, "offset must stay non-negative"
    assert bounds.get("le") == 10000, (
        "offset cap missing or changed — unbounded deep-offset scans are "
        "unauthenticated read amplification (see module docstring)"
    )
