"""Tests for the install.sh telemetry report block (fail-safe semantics).

The telemetry report is the one install.sh step that must NEVER fail the
install: the install itself is fail-closed (signature verification), but the
download-counter report is best-effort telemetry, matching the MCP
installer's `_report_install` behavior.

These tests extract the report block from install.sh (anchored on its first
line) and run it under bash with a stub `curl` on PATH, so the block is
tested as-written, not as-quoted.
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
REPORT_ANCHOR = 'echo "==> reporting install (feeds the download counter)"'


def _report_block():
    text = INSTALL_SH.read_text(encoding="utf-8")
    idx = text.index(REPORT_ANCHOR)
    return text[idx:]


def _run_report(tmp_path, curl_response, version_json, requested_version="latest"):
    """Run the extracted report block; return (rc, stdout, stderr, curl_args)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "curl_args.txt"
    stub = bin_dir / "curl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$@" > "$CURL_LOG_FILE"\n'
        'printf "%s" "$CURL_RESPONSE"\n'
    )
    stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (home / "version.json").write_text(
        json.dumps(version_json), encoding="utf-8"
    )
    env = dict(
        os.environ,
        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        CURL_LOG_FILE=str(log),
        CURL_RESPONSE=curl_response,
        SLUG="test-skill",
        VERSION=requested_version,
        API="https://example.test",
        UA="test/0.1",
        TMP=str(home),
    )
    proc = subprocess.run(
        ["bash", "-s"], input=_report_block(), capture_output=True,
        text=True, env=env, timeout=30,
    )
    args = log.read_text(encoding="utf-8") if log.exists() else ""
    return proc.returncode, proc.stdout, proc.stderr, args


def test_report_failure_still_exits_zero(tmp_path):
    """Registry outage during the report: warn, but the install stands."""
    rc, out, err, _ = _run_report(
        tmp_path,
        curl_response='{"detail":"no version \'latest\' of \'test-skill\'"}',
        version_json={"version": "1.2.3"},
    )
    assert rc == 0
    assert "WARNING" in err
    assert "install is complete" in out


def test_report_success_reports_resolved_version(tmp_path):
    """Success path posts the registry-resolved version, not 'latest'."""
    rc, out, err, args = _run_report(
        tmp_path,
        curl_response='{"ok":true,"slug":"test-skill","version":"1.2.3"}',
        version_json={"version": "1.2.3"},
    )
    assert rc == 0
    assert "reported the download" in out
    assert '"version":"1.2.3"' in args
    assert '"version":"latest"' not in args


def test_report_falls_back_to_requested_version(tmp_path):
    """version.json without a version key: fall back to the requested label."""
    rc, out, err, args = _run_report(
        tmp_path,
        curl_response='{"ok":true}',
        version_json={},
        requested_version="2.0.0",
    )
    assert rc == 0
    assert '"version":"2.0.0"' in args


def test_no_hard_exit_on_report_failure(tmp_path):
    """Regression guard: the old exit-5 telemetry failure must be gone."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    block = text[text.index(REPORT_ANCHOR):]
    assert "exit 5" not in block
