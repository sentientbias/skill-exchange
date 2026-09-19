"""Build provenance for the health endpoint (core/build_info.py).

The running service must identify itself from outside (deploy verification,
debugging) without leaking anything secret. The commit SHA of a public repo
is public information.

Run:  pytest tests/test_build_info.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import build_info as bi

RENDER_KEYS = ("RENDER_GIT_COMMIT", "RENDER_GIT_BRANCH")
FALLBACK_KEYS = ("GIT_SHA", "GIT_BRANCH")


def _clear(monkeypatch):
    for k in RENDER_KEYS + FALLBACK_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_render_vars_win(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    monkeypatch.setenv("RENDER_GIT_BRANCH", "master")
    monkeypatch.setenv("GIT_SHA", "zzz999")
    monkeypatch.setenv("GIT_BRANCH", "other")
    assert bi.build_info() == {"commit": "abc123", "branch": "master"}


def test_git_fallback_used_when_render_absent(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GIT_SHA", "def456")
    monkeypatch.setenv("GIT_BRANCH", "feature-x")
    assert bi.build_info() == {"commit": "def456", "branch": "feature-x"}


def test_unknown_when_nothing_set(monkeypatch):
    _clear(monkeypatch)
    assert bi.build_info() == {"commit": "unknown", "branch": "unknown"}


def test_empty_string_falls_through(monkeypatch):
    # An empty RENDER_GIT_COMMIT (misconfigured host) must not shadow GIT_SHA.
    _clear(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "")
    monkeypatch.setenv("GIT_SHA", "def456")
    assert bi.build_info()["commit"] == "def456"


def test_health_route_shape():
    """The /api/v1/health handler stays backward compatible and adds `build`."""
    pytest.importorskip("fastapi")
    from api.main import health
    import asyncio

    resp = asyncio.run(health())
    assert resp["ok"] is True
    assert resp["service"] == "skill-exchange"
    assert resp["version"] == "1.0.0"  # original fields untouched
    assert set(resp["build"]) == {"commit", "branch"}
