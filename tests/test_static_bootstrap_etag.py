"""ETag + 304 conditional GET on the bootstrap static routes.

/install.sh and /playbook-mcp.py are the two files every agent downloads
first, and they change only on deploy. Without cache metadata, every poll
paid for a full re-download; now a matching If-None-Match returns 304 with
no body. Same freshness convention as the feed, the sitemap, and the
skill.md readers: content-hash ETag, short public cache window.

Machine headers only: no page, copy, layout, API shape, or Pro-tier change.
The response bytes are identical to before this change.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.main as main_mod
from fastapi.testclient import TestClient

ROUTES = ["/install.sh", "/playbook-mcp.py"]


def _client():
    # The static routes never touch the DB, so the TestClient can hit them
    # without a pool. Lifespan startup is skipped: no pool is created.
    return TestClient(main_mod.app, raise_server_exceptions=False)


def test_200_carries_etag_and_cache_control():
    client = _client()
    for route in ROUTES:
        r = client.get(route)
        assert r.status_code == 200, (route, r.status_code)
        etag = r.headers.get("etag")
        assert etag and etag.startswith('"') and etag.endswith('"'), (route, etag)
        assert r.headers.get("cache-control") == "public, max-age=300", (
            route, r.headers.get("cache-control"))
        assert r.headers.get("content-type", "").startswith("text/plain"), (
            route, r.headers.get("content-type"))
        assert r.content, (route, "empty body on 200")


def test_etag_is_deterministic():
    client = _client()
    for route in ROUTES:
        a = client.get(route).headers.get("etag")
        b = client.get(route).headers.get("etag")
        assert a == b and a, (route, a, b)


def test_if_none_match_returns_304_with_no_body():
    client = _client()
    for route in ROUTES:
        etag = client.get(route).headers["etag"]
        r = client.get(route, headers={"If-None-Match": etag})
        assert r.status_code == 304, (route, r.status_code)
        assert r.content == b"", (route, "304 carried a body")
        assert r.headers.get("etag") == etag, (route, "304 dropped the etag")
        assert r.headers.get("cache-control") == "public, max-age=300", (
            route, "304 dropped cache-control")


def test_stale_or_wrong_etag_returns_200():
    client = _client()
    for route in ROUTES:
        r = client.get(route, headers={"If-None-Match": '"deadbeef"'})
        assert r.status_code == 200, (route, r.status_code)
        assert r.content, (route, "200 with no body")


def test_head_returns_headers_no_body():
    client = _client()
    for route in ROUTES:
        r = client.head(route)
        assert r.status_code == 200, (route, r.status_code)
        assert r.headers.get("etag"), (route, "HEAD dropped etag")
        assert r.content == b"", (route, "HEAD carried a body")
