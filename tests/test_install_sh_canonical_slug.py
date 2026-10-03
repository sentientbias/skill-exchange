"""Tests for install.sh's canonical-slug handling (as-written extraction).

Since the 2026-10-01 case-insensitive lookup change, the server normalizes
slugs (strip + lowercase) on read paths and signs over the canonical
(lowercase) slug. Before this fix, `./install.sh My-Skill` fetched fine
(200) but then failed client-side signature verification with a false
"SIGNATURE VERIFICATION FAILED" -- the script signed the canonical bytes
with the raw mixed-case slug the caller typed.

These tests extract the normalization + fetch + verify block from
install.sh (as-written, from the normalization line through the PYEOF
terminator) and run it under bash with env SLUG set to a mixed-case value.
The fetch's curl is stubbed on PATH to serve the test's version JSON, so
the whole flow runs with no network.
"""

import base64
import json
import os
import subprocess
from pathlib import Path

from nacl.signing import SigningKey

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
# Extract from the normalization line (34) through the PYEOF terminator so
# the whole fetch + verify flow runs as-written. The fetch's curl is stubbed
# on PATH to serve the test's version JSON, so this tests the real flow
# without network.
BLOCK_START = 'SLUG="$(printf'
BLOCK_END = "PYEOF\n"

SLUG_CANON = "test-skill"
VERSION = "1.2.3"
SKILL_MD = "# test-skill\n\nA fake skill for tests.\n"


def _verify_block():
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(BLOCK_START)
    end = text.index(BLOCK_END, start) + len(BLOCK_END)
    return text[start:end]


def _signed_payload(canonical_slug, tamper=False, include_slug_field=True):
    sk = SigningKey.generate()
    canonical = f"{canonical_slug}\n{VERSION}\n{SKILL_MD}".encode("utf-8")
    sig = sk.sign(canonical).signature
    if tamper:
        sig = bytes(b ^ 0xFF for b in sig)
    payload = {
        "skill_md": SKILL_MD,
        "signature": base64.b64encode(sig).decode(),
        "signer_pubkey": sk.verify_key.encode().hex(),
        "version": VERSION,
        "manifest": {},
    }
    if include_slug_field:
        payload["slug"] = canonical_slug
    return payload


def _run_verify_block(tmp_path, version_json, argv_slug):
    """Run the extracted fetch+verify block with a stubbed curl serving
    version_json; return (rc, stdout, stderr)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "curl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        # Write the stubbed JSON body to curl's -o target, ignore the rest.
        'out=""; prev="";\n'
        'for a in "$@"; do\n'
        '  if [ "$prev" = "-o" ]; then out="$a"; fi\n'
        '  prev="$a"\n'
        'done\n'
        'printf "%s" "$STUB_VERSION_JSON" > "$out"\n'
    )
    stub.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    dest = tmp_path / "dest"
    env = dict(
        os.environ,
        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        STUB_VERSION_JSON=json.dumps(version_json),
        TMP=str(work),
        SLUG=argv_slug,
        VERSION="latest",
        API="https://example.test",
        UA="test/0.1",
        DEST=str(dest),
    )
    proc = subprocess.run(
        ["bash", "-s"], input=_verify_block(), capture_output=True,
        text=True, env=env, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_mixed_case_slug_verifies_via_response_canonical_slug(tmp_path):
    """Mixed-case SLUG argv + response carrying the canonical slug: the
    signature's canonical bytes use the response slug, not the raw argv."""
    payload = _signed_payload(SLUG_CANON)
    rc, out, err = _run_verify_block(tmp_path, payload, argv_slug="Test-Skill")
    assert rc == 0, f"expected success, stderr: {err}"
    assert "VERIFIED_OK" in out
    md_path = tmp_path / "dest" / SLUG_CANON / "SKILL.md"
    assert md_path.read_text(encoding="utf-8") == SKILL_MD


def test_missing_response_slug_falls_back_to_normalized_argv(tmp_path):
    """Payloads without a `slug` field (older format) verify against the
    bash-normalized argv slug -- pins the `tr 'A-Z' 'a-z'` line too."""
    payload = _signed_payload(SLUG_CANON, include_slug_field=False)
    rc, out, err = _run_verify_block(tmp_path, payload, argv_slug="Test-Skill")
    assert rc == 0, f"fallback failed, stderr: {err}"
    assert "VERIFIED_OK" in out


def test_tampered_signature_still_fails_closed(tmp_path):
    """The canonical-slug preference must not weaken fail-closed behavior."""
    payload = _signed_payload(SLUG_CANON, tamper=True)
    rc, out, err = _run_verify_block(tmp_path, payload, argv_slug="Test-Skill")
    assert rc == 4, f"expected exit 4, got {rc}, stderr: {err}"
    assert "SIGNATURE VERIFICATION FAILED" in err
    assert list((tmp_path / "dest").iterdir()) == [] if (
        tmp_path / "dest").exists() else True


def test_install_dir_uses_canonical_slug_not_raw_argv(tmp_path):
    """The install directory follows the response's canonical slug.

    The shell normalizes case (`tr A-Z a-z`) but NOT whitespace, while the
    server's read paths do strip + lowercase. Before this fix the directory
    came from the raw argv slug, so an argv like "test-skill " (trailing
    space) verified the signature over the canonical "test-skill" but wrote
    into a "test-skill /" directory -- the signed identity and the install
    location disagreed, and the same skill could land in two directories.
    Now the destination directory always uses the canonical slug the
    signature binds, matching the signature's canonical-bytes rule.
    """
    payload = _signed_payload(SLUG_CANON)
    rc, out, err = _run_verify_block(tmp_path, payload, argv_slug="test-skill ")
    assert rc == 0, f"expected success, stderr: {err}"
    assert "VERIFIED_OK" in out
    canon_dir = tmp_path / "dest" / SLUG_CANON
    assert (canon_dir / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    # The raw-argv directory (with the trailing space) must not exist.
    assert not (tmp_path / "dest" / "test-skill ").exists(), (
        "install used the raw argv slug for the destination directory"
    )


def test_install_dir_fallback_without_response_slug(tmp_path):
    """Without a response `slug` field, the directory falls back to the
    bash-normalized argv slug -- the same fallback the signature check
    uses, so the two can never disagree."""
    payload = _signed_payload(SLUG_CANON, include_slug_field=False)
    rc, out, err = _run_verify_block(tmp_path, payload, argv_slug="Test-Skill")
    assert rc == 0, f"expected success, stderr: {err}"
    assert "VERIFIED_OK" in out
    assert (tmp_path / "dest" / SLUG_CANON / "SKILL.md").read_text(
        encoding="utf-8") == SKILL_MD
