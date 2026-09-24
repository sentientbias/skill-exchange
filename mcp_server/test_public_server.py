"""Self-tests for public_server.py. No network access -- the HTTP layer is
monkeypatched. Run:  python -m pytest mcp_server/test_public_server.py -q
(or: python mcp_server/test_public_server.py)
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import subprocess
import sys
import urllib.error
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp_server.public_server as ps
from mcp_server.public_server import PlaybookError

SLUG = "test-skill"
VERSION = "1.2.3"
SKILL_MD = "# test-skill\n\nA fake skill for tests.\n"


def _make_version_json(skill_md=SKILL_MD, tamper=False):
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    canonical = f"{SLUG}\n{VERSION}\n{skill_md}".encode("utf-8")
    sig = sk.sign(canonical).signature
    if tamper:
        sig = bytes(b ^ 0xFF for b in sig)  # flip every bit -> invalid
    return {
        "skill_md": skill_md,
        "signature": base64.b64encode(sig).decode(),
        "signer_pubkey": sk.verify_key.encode().hex(),
        "version": VERSION,
        "manifest": {"name": SLUG},
    }, sk


def _mock_http(payload):
    def fake(path, params=None):
        return payload

    return mock.patch.object(ps, "_http_get_json", side_effect=fake)


def _mock_http_with_report(payload, report_ok=True, report_exc=None):
    """Mock GET layer (like _mock_http) plus the install-report POST layer.

    Returns a context manager patching both, and a dict capturing the POST
    path/payload so tests can assert exactly what was reported.
    """
    captured = {}

    def fake_get(path, params=None):
        return payload

    def fake_post(path, post_payload):
        captured["path"] = path
        captured["payload"] = post_payload
        if report_exc is not None:
            raise report_exc
        return {"ok": report_ok, "slug": SLUG, "version": VERSION}

    class _Ctx:
        def __enter__(self):
            # NB: keep the PATCHER (not the mock __enter__ returns), or the
            # patches leak into later tests -- __enter__ returns the mock.
            self._g = mock.patch.object(ps, "_http_get_json",
                                        side_effect=fake_get)
            self._g.__enter__()
            self._p = mock.patch.object(ps, "_http_post_json",
                                        side_effect=fake_post)
            self._p.__enter__()
            return captured

        def __exit__(self, *exc):
            self._p.__exit__(*exc)
            self._g.__exit__(*exc)
            return False

    return _Ctx()


def test_fail_closed_when_pynacl_missing(tmp_path):
    """(a) No nacl -> clear error, nothing written."""
    payload, _ = _make_version_json()
    blocked = {"nacl": None, "nacl.signing": None, "nacl.exceptions": None}
    with _mock_http(payload), mock.patch.dict(sys.modules, blocked):
        with pytest.raises(PlaybookError, match="pynacl"):
            ps.install_skill(SLUG, VERSION, dest_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == [], "nothing may be written on failure"


def test_fail_closed_on_bad_signature(tmp_path):
    """(b) Tampered signature -> clear error, nothing written."""
    payload, _ = _make_version_json(tamper=True)
    with _mock_http(payload):
        with pytest.raises(PlaybookError, match="SIGNATURE VERIFICATION FAILED"):
            ps.install_skill(SLUG, VERSION, dest_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == [], "nothing may be written on failure"


def test_fail_closed_on_missing_fields(tmp_path):
    """Missing version fields -> clear error, nothing written."""
    with _mock_http({"skill_md": SKILL_MD}):  # no signature/pubkey/version
        with pytest.raises(PlaybookError, match="missing"):
            ps.install_skill(SLUG, VERSION, dest_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_success_path_writes_verified_bytes(tmp_path):
    """(c) Valid signature -> SKILL.md + manifest.json with exact bytes."""
    payload, _ = _make_version_json()
    with _mock_http_with_report(payload):
        result = ps.install_skill(SLUG, VERSION, dest_dir=str(tmp_path))
    assert result["verified"] is True
    assert result["version"] == VERSION
    md_path = os.path.join(str(tmp_path), "SKILL.md")
    assert result["path"] == md_path
    with open(md_path, encoding="utf-8") as fh:
        assert fh.read() == SKILL_MD, "written bytes must equal signed skill_md"
    manifest_path = os.path.join(str(tmp_path), "manifest.json")
    assert os.path.exists(manifest_path)


def test_install_reports_to_registry(tmp_path):
    """Verified install reports itself: POST /api/v1/installs with
    slug, resolved version, and client tag."""
    payload, _ = _make_version_json()
    with _mock_http_with_report(payload) as captured:
        result = ps.install_skill(SLUG, VERSION, dest_dir=str(tmp_path))
    assert result["reported"] is True
    assert captured["path"] == "/api/v1/installs"
    assert captured["payload"]["slug"] == SLUG
    assert captured["payload"]["version"] == VERSION  # resolved, not "latest"
    assert captured["payload"]["client"] == "mcp/1.0"
    assert "counts as a download" in result["note"]


def test_install_succeeds_when_report_fails(tmp_path):
    """Telemetry failure never undoes a verified install: files written,
    verified=True, reported=False with an honest reason."""
    payload, _ = _make_version_json()
    with _mock_http_with_report(
        payload, report_exc=PlaybookError("boom")
    ) as captured:
        result = ps.install_skill(SLUG, VERSION, dest_dir=str(tmp_path))
    assert result["verified"] is True
    assert result["reported"] is False
    assert captured["path"] == "/api/v1/installs"  # the attempt was made
    md_path = os.path.join(str(tmp_path), "SKILL.md")
    with open(md_path, encoding="utf-8") as fh:
        assert fh.read() == SKILL_MD
    assert "NOT counted" in result["note"]


def test_install_reports_resolved_version_not_requested(tmp_path):
    """install_skill(slug, "latest") reports the RESOLVED version the
    registry returned, so the counter lands on the right row."""
    payload, _ = _make_version_json()
    with _mock_http_with_report(payload) as captured:
        ps.install_skill(SLUG, "latest", dest_dir=str(tmp_path))
    assert captured["payload"]["version"] == VERSION


def test_whats_new_parses_7d_to_iso():
    """(d) '7d' -> ISO-8601 UTC ~7 days ago, passed as the since param."""
    captured = {}

    def fake(path, params=None):
        captured.update(params or {})
        return {"items": []}

    with mock.patch.object(ps, "_http_get_json", side_effect=fake):
        result = ps.whats_new("7d")
    assert result["since"] == captured["since"]
    parsed = dt.datetime.fromisoformat(captured["since"])
    assert parsed.tzinfo is not None, "must be timezone-aware"
    expected = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    assert abs((parsed - expected).total_seconds()) < 120
    assert captured["sort"] == "newest" and captured["limit"] == 20


def test_whats_new_rejects_garbage():
    with pytest.raises(PlaybookError, match="Invalid since"):
        ps.whats_new("yesterday-ish")


def test_exits_with_clear_message_when_mcp_missing():
    """Running the script without `mcp` installed -> clear stderr + exit 1."""
    if ps._MCP_OK:  # pragma: no cover
        pytest.skip("mcp is installed in this env")
    proc = subprocess.run(
        [sys.executable, ps.__file__],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(
            [os.path.dirname(os.path.dirname(os.path.abspath(ps.__file__)))]
        )},
    )
    assert proc.returncode == 1
    assert 'pip install "mcp"' in proc.stderr


def _http_404(path, detail):
    """Build an urllib.error.HTTPError like the live API's recoverable 404s."""
    body = json.dumps({"detail": detail}).encode("utf-8")
    return urllib.error.HTTPError(
        "https://api.example.test" + path, 404, "Not Found", {},
        io.BytesIO(body),
    )


def test_error_message_surfaces_suggestions():
    msg = ps._http_error_message(
        "GET", "/api/v1/skills/regex-mastrery", 404,
        json.dumps({"detail": {
            "message": "no skill 'regex-mastrery'; did you mean: 'regex-mastery'?",
            "suggestions": ["regex-mastery"],
        }}).encode("utf-8"),
    )
    assert "HTTP 404" in msg
    assert "suggestions: regex-mastery" in msg
    assert "did you mean" in msg


def test_error_message_surfaces_available_versions():
    msg = ps._http_error_message(
        "GET", "/api/v1/skills/regex-mastery/versions/9.9.9", 404,
        json.dumps({"detail": {
            "message": "no version '9.9.9' of 'regex-mastery'; "
                       "available versions: 1.0.0",
            "available_versions": ["1.0.0"],
        }}).encode("utf-8"),
    )
    assert "HTTP 404" in msg
    assert "available versions: 1.0.0" in msg


def test_error_message_handles_string_detail():
    msg = ps._http_error_message(
        "POST", "/api/v1/installs", 429,
        json.dumps({"detail": "rate limit exceeded"}).encode("utf-8"),
    )
    assert msg == ("Playbook API POST /api/v1/installs failed: HTTP 429"
                   " -- rate limit exceeded")


def test_error_message_falls_back_on_garbage_body():
    for body in (b"", b"<html>proxy error</html>",
                 json.dumps({"error": "weird"}).encode("utf-8")):
        msg = ps._http_error_message("GET", "/api/v1/skills", 502, body)
        assert msg == "Playbook API GET /api/v1/skills failed: HTTP 502"


def test_get_skill_404_surfaces_suggestions():
    """A typo'd slug through get_skill must raise the did-you-mean
    recovery payload, not a bare HTTP 404."""
    err = _http_404("/api/v1/skills/regex-mastrery", {
        "message": "no skill 'regex-mastrery'; did you mean: 'regex-mastery'?",
        "suggestions": ["regex-mastery"],
    })
    with mock.patch.object(ps.urllib.request, "urlopen", side_effect=err):
        with pytest.raises(PlaybookError, match="HTTP 404") as exc:
            ps.get_skill("regex-mastrery")
    assert "suggestions: regex-mastery" in str(exc.value)


def test_install_skill_404_surfaces_available_versions(tmp_path):
    """A bogus version through install_skill must list what exists."""
    err = _http_404("/api/v1/skills/regex-mastery/versions/9.9.9", {
        "message": "no version '9.9.9' of 'regex-mastery'; "
                   "available versions: 1.0.0",
        "available_versions": ["1.0.0"],
    })
    with mock.patch.object(ps.urllib.request, "urlopen", side_effect=err):
        with pytest.raises(PlaybookError, match="HTTP 404") as exc:
            ps.install_skill(SLUG, "9.9.9", dest_dir=str(tmp_path))
    assert "available versions: 1.0.0" in str(exc.value)
    assert list(tmp_path.iterdir()) == [], "nothing may be written on failure"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
