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


# --- fetch block: surface the registry's recoverable-404 hint -------------

FETCH_ANCHOR = 'echo "==> fetching $SLUG v$VERSION from $API"'
VERIFY_ANCHOR = 'echo "==> verifying ed25519 signature (fail closed)"'


def _fetch_block():
    text = INSTALL_SH.read_text(encoding="utf-8")
    return text[text.index(FETCH_ANCHOR):text.index(VERIFY_ANCHOR)]


def _run_fetch(tmp_path, fail_body, fail_exit=22):
    """Run the extracted fetch block with a stub curl.

    The stub answers the --help probe as an old curl (no --fail-with-body)
    and fails the fetch with fail_body / fail_exit.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "curl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "--help" ]; then exit 0; fi\n'
        'printf "%s" "$CURL_FAIL_BODY"\n'
        "exit \"$CURL_FAIL_EXIT\"\n"
    )
    stub.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = dict(
        os.environ,
        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        CURL_FAIL_BODY=fail_body,
        CURL_FAIL_EXIT=str(fail_exit),
        SLUG="regex-mastrery",
        VERSION="latest",
        API="https://example.test",
        UA="test/0.1",
        TMP=str(home),
    )
    proc = subprocess.run(
        ["bash", "-s"], input=_fetch_block(), capture_output=True,
        text=True, env=env, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_fetch_surfaces_did_you_mean(tmp_path):
    """Typo'd slug: the registry's did-you-mean hint reaches the user."""
    rc, out, err, = _run_fetch(
        tmp_path,
        fail_body=('{"detail":{"message":"no skill \'regex-mastrery\'; '
                   'did you mean: \'regex-mastery\'?",'
                   '"suggestions":["regex-mastery"]}}'),
    )
    assert rc == 6
    assert "could not fetch regex-mastrery vlatest" in err
    assert "did you mean: 'regex-mastery'?" in err


def test_fetch_surfaces_available_versions(tmp_path):
    """Unknown version: the registry's available-versions hint reaches the user."""
    rc, out, err = _run_fetch(
        tmp_path,
        fail_body=('{"detail":{"message":"no version \'9.9.9\' of '
                   '\'regex-mastery\'; available versions: 1.0.0",'
                   '"available_versions":["1.0.0"]}}'),
    )
    assert rc == 6
    assert "available versions: 1.0.0" in err


def test_fetch_handles_string_detail(tmp_path):
    """Plain-string detail shape (older FastAPI style) is printed as-is."""
    rc, out, err = _run_fetch(
        tmp_path, fail_body='{"detail":"service is restarting"}'
    )
    assert rc == 6
    assert "service is restarting" in err


def test_fetch_falls_back_on_unreadable_body(tmp_path):
    """HTML/garbage body: no parse, honest fallback line, still exit 6."""
    rc, out, err = _run_fetch(tmp_path, fail_body="<html>nope</html>")
    assert rc == 6
    assert "registry unreachable or the error was unreadable" in err


def test_fetch_failure_never_writes_partial(tmp_path):
    """A failed fetch leaves no version.json behind for the verifier."""
    rc, out, err = _run_fetch(tmp_path, fail_body="{}")
    assert rc == 6
    assert not (tmp_path / "home" / "version.json").exists()
