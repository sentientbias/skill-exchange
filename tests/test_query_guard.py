"""Tests for api.query_guard.RejectUnknownQueryParamsMiddleware.

Runs against a tiny throwaway FastAPI app (no DB), plus a static check that
the real app's /api/v1/skills route declares exactly the documented params.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient

from api.query_guard import RejectUnknownQueryParamsMiddleware


def _tiny_app():
    app = FastAPI()
    app.add_middleware(RejectUnknownQueryParamsMiddleware)

    def _paging(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return limit, offset

    @app.get("/api/v1/skills")
    def list_skills(q: str = Query(default=""), paging=Depends(_paging)):
        return {"q": q, "paging": paging}

    @app.get("/api/v1/skills/{slug}")
    def get_skill(slug: str):
        return {"slug": slug}

    @app.get("/not-api")
    def not_api(whatever: str = Query(default="")):
        return {"ok": True}

    return app


def test_unknown_param_rejected_with_422():
    client = TestClient(_tiny_app())
    r = client.get("/api/v1/skills", params={"pack": "nonexistent_pack_xyz"})
    assert r.status_code == 422, r.text
    assert "pack" in r.json()["detail"]


def test_known_params_pass():
    client = TestClient(_tiny_app())
    r = client.get("/api/v1/skills", params={"q": "debugging", "limit": "5"})
    assert r.status_code == 200, r.text


def test_known_param_from_sub_dependency_passes():
    client = TestClient(_tiny_app())
    r = client.get("/api/v1/skills", params={"offset": "10"})
    assert r.status_code == 200, r.text


def test_path_param_route_rejects_unknown_query():
    client = TestClient(_tiny_app())
    r = client.get("/api/v1/skills/some-slug", params={"pack": "x"})
    assert r.status_code == 422, r.text


def test_non_api_paths_untouched():
    client = TestClient(_tiny_app())
    r = client.get("/not-api", params={"anything": "goes"})
    assert r.status_code == 200, r.text


def test_real_app_skills_route_params():
    # Import-safe: api.main connects to the DB only in lifespan, not at import.
    from api.main import app

    from api.query_guard import _known_query_params, _match_route

    route = _match_route(app.routes, "/api/v1/skills", "GET")
    assert route is not None
    assert _known_query_params(route.dependant) == {
        "q",
        "category",
        "sort",
        "limit",
        "offset",
        "since",
    }
