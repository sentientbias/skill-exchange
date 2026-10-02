"""Tests for install.sh's 404 "did you mean" hint (as-written extraction).

The server's 404 for an unknown slug returns a readable hint
("no skill 'regex-mastr'; did you mean: 'regex-mastery'?"), and install.sh
intended to surface it via curl's --fail-with-body. But the fetch also
passes -o "$TMP/version.json", and --fail-with-body saves the error body
into the -o file, NOT stdout -- so ERRBODY was always empty and every 404
told the user "(registry unreachable or the error was unreadable)".

The fix: read the error body from $TMP/version.json first (what
--fail-with-body actually populated), falling back to the captured stdout
for older -f curls that discard the body entirely.

These tests extract the fetch-failure block from install.sh as-written
and run it under bash with a stubbed curl on PATH, so no network is used.
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
BLOCK_START = 'if ! ERRBODY="$(curl'

ERROR_404 = {
    "detail": {
        "message": "no skill 'regex-mastr'; did you mean: 'regex-mastery'?",
        "suggestions": ["regex-mastery"],
    }
}


def _failure_block():
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(BLOCK_START)
    end = text.index("\nfi\n", start) + len("\nfi\n")
    return text[start:end]


def _run_failure_block(tmp_path, mode, body):
    """Run the extracted failure block with a stubbed curl.

    mode="file": stub writes body to curl's -o file, nothing to stdout,
        exit 22 (real --fail-with-body + -o behavior).
    mode="stdout": stub prints body to stdout, exit 22 (hypothetical
        curl variant that keeps the body on stdout).
    mode="empty": stub exits 22 with no body anywhere (real old -f curl).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "curl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--help" ]; then\n'
        '  echo "supports --fail-with-body"\n'
        "  exit 0\n"
        "fi\n"
        'out=""; prev=""\n'
        'for a in "$@"; do\n'
        '  if [ "$prev" = "-o" ]; then out="$a"; fi\n'
        '  prev="$a"\n'
        "done\n"
        'if [ "$STUB_MODE" = "file" ]; then\n'
        '  printf "%s" "$STUB_BODY" > "$out"\n'
        'elif [ "$STUB_MODE" = "stdout" ]; then\n'
        '  printf "%s" "$STUB_BODY"\n'
        "fi\n"
        "exit 22\n"
    )
    stub.chmod(0o755)

    script = (
        "TMP=\"$(mktemp -d)\"\n"
        "API=\"https://registry.invalid\"\n"
        'SLUG="regex-mastr"\n'
        'VERSION="latest"\n'
        'UA="test"\n'
        + _failure_block()
    )
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["STUB_MODE"] = mode
    env["STUB_BODY"] = body
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


def test_fail_with_body_file_hint_shown(tmp_path):
    """--fail-with-body writes the 404 body to -o file: the hint must show."""
    proc = _run_failure_block(tmp_path, "file", json.dumps(ERROR_404))
    assert proc.returncode == 6
    assert "did you mean: 'regex-mastery'?" in proc.stderr
    assert "unreadable" not in proc.stderr


def test_stdout_fallback_hint_shown(tmp_path):
    """Body on stdout (no -o write) must still reach the user."""
    proc = _run_failure_block(tmp_path, "stdout", json.dumps(ERROR_404))
    assert proc.returncode == 6
    assert "did you mean: 'regex-mastery'?" in proc.stderr


def test_empty_body_unreachable_message(tmp_path):
    """No body anywhere (old -f curl): the unreachable fallback must show."""
    proc = _run_failure_block(tmp_path, "empty", "")
    assert proc.returncode == 6
    assert "(registry unreachable or the error was unreadable)" in proc.stderr


def test_garbage_body_unreachable_message(tmp_path):
    """Non-JSON error body: no crash, the unreachable fallback must show."""
    proc = _run_failure_block(tmp_path, "file", "<html>502 bad gateway")
    assert proc.returncode == 6
    assert "(registry unreachable or the error was unreadable)" in proc.stderr
