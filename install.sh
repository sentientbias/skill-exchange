#!/usr/bin/env bash
# install.sh — install a skill from The Playbook (Skill Exchange) without MCP.
#
# Usage:  ./install.sh <slug> [version] [dest-dir]
#
#   slug      skill slug, e.g. regex-mastery
#   version   default: latest
#   dest-dir  default: ~/workspace/skills/installed
#
# Flow: fetch the signed package -> VERIFY the ed25519 signature client-side
# -> save SKILL.md -> report the install (feeds the registry download counter).
#
# The report is best-effort telemetry, matching the MCP installer's
# fail-safe semantics: a registry outage never undoes a verified install
# (exit 0 either way). Verification, by contrast, is mandatory and
# fail-closed.
#
# Verification is mandatory. If pynacl is not available, this script FAILS
# CLOSED rather than installing unverified bytes.
set -euo pipefail

API="${SKILL_EXCHANGE_API:-https://skill-exchange-api-hoev.onrender.com}"
SLUG="${1:?usage: $0 <slug> [version] [dest-dir]}"
VERSION="${2:-latest}"
DEST="${3:-$HOME/workspace/skills/installed}"
# The server's read paths normalize slugs (strip + lowercase, npm/PyPI
# convention) since the 2026-10-01 case-insensitive lookup change. Mirror the
# lowercase here so a mixed-case slug (copied from a display name) fetches
# fine and, critically, so verification below signs over the same canonical
# slug the server signed with. Without this, `./install.sh My-Skill` fetched
# OK (200) but failed verification with a scary false
# "SIGNATURE VERIFICATION FAILED". The verify block below additionally
# prefers the response's canonical slug when present (belt and braces).
SLUG="$(printf '%s' "$SLUG" | tr 'A-Z' 'a-z')"
UA="skill-exchange-install-sh/1.0"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> fetching $SLUG v$VERSION from $API"
# --fail-with-body (curl 7.76+) keeps the registry's error body on HTTP
# failures so a recoverable 404's hint ("did you mean ...?") reaches the
# user instead of a bare curl 22. Older curls fall back to -f.
CURL_FAIL_FLAG="-f"
if curl --help all 2>/dev/null | grep -q -- "--fail-with-body"; then
  CURL_FAIL_FLAG="--fail-with-body"
fi
if ! ERRBODY="$(curl -sS "$CURL_FAIL_FLAG" -A "$UA" --max-time 60 \
  "$API/api/v1/skills/$SLUG/versions/$VERSION" -o "$TMP/version.json")"; then
  # --fail-with-body saves the error body into the -o file, NOT stdout, so
  # ERRBODY is empty on modern curls: prefer the saved file, fall back to
  # the captured stdout (older -f curls discard the body entirely).
  ERRTEXT="$ERRBODY"
  if [ -s "$TMP/version.json" ]; then ERRTEXT="$(cat "$TMP/version.json")"; fi
  HINT="$(printf '%s' "$ERRTEXT" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    det = d.get("detail")
    if isinstance(det, dict):
        msg = det.get("message", "")
    elif isinstance(det, str):
        msg = det
    else:
        msg = ""
    if msg:
        print(msg)
except Exception:
    pass' 2>/dev/null)"
  echo "ERROR: could not fetch $SLUG v$VERSION from $API" >&2
  if [ -n "${HINT:-}" ]; then
    echo "  $HINT" >&2
  else
    echo "  (registry unreachable or the error was unreadable)" >&2
  fi
  exit 6
fi

echo "==> verifying ed25519 signature (fail closed)"
python3 - "$TMP/version.json" "$SLUG" "$DEST" <<'PYEOF'
import base64, binascii, json, os, sys

try:
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey
except ImportError:
    print("ERROR: pynacl is not installed (pip install pynacl). "
          "Refusing to install without signature verification.",
          file=sys.stderr)
    sys.exit(2)

ver_path, slug, dest = sys.argv[1], sys.argv[2], sys.argv[3]
with open(ver_path, encoding="utf-8") as fh:
    ver = json.load(fh)

skill_md  = ver.get("skill_md") or ""
signature = ver.get("signature") or ""
pubkey    = ver.get("signer_pubkey") or ""
version   = ver.get("version") or ""
if not (skill_md and signature and pubkey and version):
    print("ERROR: registry response missing skill_md/signature/public_key/"
          "version -- refusing to install", file=sys.stderr)
    sys.exit(3)

# Use the response's canonical slug for the signature's canonical bytes
# AND for the install directory, not whatever case the caller typed: the
# server normalizes slugs on read paths, and its signatures bind the
# canonical (lowercase) form. Fall back to the argv slug for payloads that
# predate the field. The directory must follow the same canonical name --
# otherwise a mixed-case (or whitespace-padded) argv would verify over
# "test-skill" but write into "Test-Skill/" or "test-skill /", so the same
# signed skill could land in two directories and update loops keyed on the
# registry slug would miss it.
canon_slug = ver.get("slug") or slug
canonical = f"{canon_slug}\n{version}\n{skill_md}".encode("utf-8")
try:
    VerifyKey(bytes.fromhex(pubkey.strip())).verify(
        canonical, base64.b64decode(signature.strip()))
except (BadSignatureError, ValueError, binascii.Error):
    print("ERROR: SIGNATURE VERIFICATION FAILED -- package does not match "
          "the publisher's public key. Refusing to install.",
          file=sys.stderr)
    sys.exit(4)

skill_dir = os.path.join(os.path.expanduser(dest), canon_slug)
os.makedirs(skill_dir, exist_ok=True)
md_path = os.path.join(skill_dir, "SKILL.md")
with open(md_path, "w", encoding="utf-8") as fh:
    fh.write(skill_md)
if ver.get("manifest"):
    with open(os.path.join(skill_dir, "manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(ver["manifest"], fh, indent=2, default=str)

print(f"VERIFIED_OK version={version} path={md_path}")
PYEOF

echo "==> reporting install (feeds the download counter)"
RESOLVED_VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("version") or "")' "$TMP/version.json" 2>/dev/null || true)"
RESOLVED_VERSION="${RESOLVED_VERSION:-$VERSION}"
RESP="$(curl -sS -A "$UA" --max-time 60 -X POST "$API/api/v1/installs" \
  -H 'Content-Type: application/json' \
  -d "{\"slug\":\"$SLUG\",\"version\":\"$RESOLVED_VERSION\",\"client\":\"install-sh/1.0\"}")"
if echo "$RESP" | grep -q '"ok":[ ]*true'; then
  echo "==> done: installed $SLUG $RESOLVED_VERSION and reported the download"
else
  echo "WARNING: install saved but the download-counter report failed: $RESP" >&2
  echo "==> done: installed $SLUG $RESOLVED_VERSION (report not recorded; install is complete)"
fi
