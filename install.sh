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
# Verification is mandatory. If pynacl is not available, this script FAILS
# CLOSED rather than installing unverified bytes.
set -euo pipefail

API="${SKILL_EXCHANGE_API:-https://skill-exchange-api-hoev.onrender.com}"
SLUG="${1:?usage: $0 <slug> [version] [dest-dir]}"
VERSION="${2:-latest}"
DEST="${3:-$HOME/workspace/skills/installed}"
UA="skill-exchange-install-sh/1.0"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> fetching $SLUG v$VERSION from $API"
curl -sSf -A "$UA" --max-time 60 \
  "$API/api/v1/skills/$SLUG/versions/$VERSION" -o "$TMP/version.json"

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

canonical = f"{slug}\n{version}\n{skill_md}".encode("utf-8")
try:
    VerifyKey(bytes.fromhex(pubkey.strip())).verify(
        canonical, base64.b64decode(signature.strip()))
except (BadSignatureError, ValueError, binascii.Error):
    print("ERROR: SIGNATURE VERIFICATION FAILED -- package does not match "
          "the publisher's public key. Refusing to install.",
          file=sys.stderr)
    sys.exit(4)

skill_dir = os.path.join(os.path.expanduser(dest), slug)
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
RESP="$(curl -sS -A "$UA" --max-time 60 -X POST "$API/api/v1/installs" \
  -H 'Content-Type: application/json' \
  -d "{\"slug\":\"$SLUG\",\"version\":\"$VERSION\",\"client\":\"install-sh/1.0\"}")"
echo "$RESP" | grep -q '"ok":[ ]*true' \
  && echo "==> done: installed $SLUG and reported the download" \
  || { echo "WARNING: install saved but report failed: $RESP" >&2; exit 5; }
