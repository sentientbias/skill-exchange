"""Interactive API docs (/docs, /redoc, /openapi.json) are dev-only.

Design under test: FastAPI serves a full route/schema map plus a browser
"Try it out" client by default. On the public registry that is a recon
amplifier (OWASP A05), so the docs routes are registered only when
ENABLE_API_DOCS=1 — off by default, off on the production Render service.

Run:  pytest tests/test_api_docs.py
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def _fresh_app(enable_docs):
    """Re-import api.main with ENABLE_API_DOCS set/unset.

    The FastAPI app is built at import time, so the flag must be decided
    before import. The original module is restored on exit so other test
    files keep importing the app they expect.
    """
    import api.main as main_mod

    original = sys.modules["api.main"]
    try:
        if enable_docs:
            os.environ["ENABLE_API_DOCS"] = enable_docs
        else:
            os.environ.pop("ENABLE_API_DOCS", None)
        importlib.reload(main_mod)
        return main_mod.app
    finally:
        sys.modules["api.main"] = original
        os.environ.pop("ENABLE_API_DOCS", None)


def test_docs_disabled_by_default():
    app = _fresh_app(None)
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    paths = {getattr(r, "path", None) for r in app.routes}
    assert not (set(_DOCS_PATHS) & paths)
    client = TestClient(app)
    for path in _DOCS_PATHS:
        assert client.get(path).status_code == 404, path


def test_docs_enabled_with_flag():
    app = _fresh_app("1")
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert "paths" in schema.json()


def test_flag_truthy_variants():
    for value in ("true", "TRUE", "yes", "on", " 1 "):
        app = _fresh_app(value)
        assert app.docs_url == "/docs", value


def test_flag_falsy_variants():
    for value in ("0", "false", "no", "", "maybe"):
        app = _fresh_app(value)
        assert app.docs_url is None, value


def test_docs_not_registered_as_redirects():
    # None URLS must remove the routes entirely — no 307s leaking existence
    # to a visitor probing for them.
    app = _fresh_app(None)
    client = TestClient(app, follow_redirects=False)
    for path in _DOCS_PATHS:
        r = client.get(path)
        assert r.status_code == 404, (path, r.status_code)
