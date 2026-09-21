"""Self-tests for public_server.py. No network access -- the HTTP layer is
monkeypatched. Run:  python -m pytest mcp_server/test_public_server.py -q
(or: python mcp_server/test_public_server.py)
"""

from __future__ import annotations

import base64
import datetime as dt
import os
import subprocess
import sys
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
    with _mock_http(payload):
        result = ps.install_skill(SLUG, VERSION, dest_dir=str(tmp_path))
    assert result["verified"] is True
    assert result["version"] == VERSION
    md_path = os.path.join(str(tmp_path), "SKILL.md")
    assert result["path"] == md_path
    with open(md_path, encoding="utf-8") as fh:
        assert fh.read() == SKILL_MD, "written bytes must equal signed skill_md"
    manifest_path = os.path.join(str(tmp_path), "manifest.json")
    assert os.path.exists(manifest_path)


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
