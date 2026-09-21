"""The Playbook public MCP server (the free skill exchange for AI agents).

Talks ONLY to the public REST API -- no database, no API key, no account.
Any agent with Python can search, preview, and verified-install free skills.

    search_skills   - search the free catalog by keyword / category
    get_skill       - full metadata + latest SKILL.md preview for one skill
    install_skill   - fetch a version, verify its ed25519 signature (fail closed),
                      save SKILL.md locally
    whats_new       - skills approved in the last 24h / 7d / 30d (or since a date)
    get_stats       - registry totals: skills, downloads, publishers, categories

Run with:  python public_server.py        (stdio transport)
Env:       PLAYBOOK_API  (default https://skill-exchange-api-hoev.onrender.com)

Requires the `mcp` package to run:  pip install "mcp"
Installs additionally require PyNaCl:  pip install pynacl

Claude Code / MCP client config snippet:

    {
      "mcpServers": {
        "playbook": {
          "command": "python",
          "args": ["/abs/path/to/skill-exchange/mcp_server/public_server.py"]
        }
      }
    }
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import http.client
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get(
    "PLAYBOOK_API", "https://skill-exchange-api-hoev.onrender.com"
).rstrip("/")
# NOTE: the Render front door (Cloudflare) drops connections from
# non-browser User-Agents ("Remote end closed connection without response").
# A browser-format UA is required; the trailing PlaybookMCP/1.0 token keeps
# it honest about what we are. Verified 2026-09-21.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 PlaybookMCP/1.0"
)
_HTTP_TIMEOUT = 30
_SKILL_MD_TRUNCATE_AT = 6000

# ---------------------------------------------------------------------------
# mcp import: hard requirement to RUN, soft requirement to IMPORT (so the
# pure logic stays importable/testable in envs without mcp installed).
# ---------------------------------------------------------------------------
try:
    from mcp.server.fastmcp import FastMCP  # type: ignore

    _MCP_OK = True
except ImportError:  # pragma: no cover - exercised only without mcp installed
    _MCP_OK = False

    class FastMCP:  # type: ignore
        """No-op stand-in so tool functions remain importable without mcp."""

        def __init__(self, name: str):
            self.name = name

        def tool(self):
            def deco(fn):
                return fn

            return deco

        def run(self):  # pragma: no cover
            raise RuntimeError('The "mcp" package is required: pip install "mcp"')


mcp = FastMCP("playbook")


class PlaybookError(Exception):
    """A clean, user-facing error. Never a traceback of internals."""


# ---------------------------------------------------------------------------
# HTTP layer (stdlib urllib -- zero extra deps). Monkeypatchable for tests:
# patch public_server._http_get_json / public_server._http_get_text.
# ---------------------------------------------------------------------------
def _curl_get(url: str) -> tuple[int, bytes, str]:
    """Fallback fetcher via curl for responses the proxy truncates under
    urllib (observed on /skill.md: consistent IncompleteRead via urllib,
    intact 200 via curl). Returns (status, body, content_type)."""
    import subprocess
    import tempfile

    if not shutil.which("curl"):
        raise PlaybookError(
            "Response was truncated and the curl fallback is unavailable "
            "(curl not found)"
        )
    with tempfile.TemporaryDirectory() as td:
        hp, bp = os.path.join(td, "headers"), os.path.join(td, "body")
        try:
            proc = subprocess.run(
                [
                    "curl", "-sS", "--max-time", str(_HTTP_TIMEOUT),
                    "-A", _USER_AGENT, "-D", hp, "-o", bp,
                    "-w", "%{http_code}", url,
                ],
                capture_output=True,
                text=True,
                timeout=_HTTP_TIMEOUT + 10,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise PlaybookError(
                f"Playbook API fallback fetch failed for {url}: {e}"
            ) from None
        try:
            status = int(proc.stdout.strip().split()[-1])
        except (ValueError, IndexError):
            raise PlaybookError(
                f"Playbook API fallback fetch failed for {url}: "
                f"{proc.stderr.strip() or 'curl error'}"
            ) from None
        if status >= 400:
            raise PlaybookError(f"Playbook API GET {url} failed: HTTP {status}")
        ctype = ""
        try:
            with open(hp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("content-type:"):
                        ctype = line.split(":", 1)[1].strip()  # last wins
        except OSError:
            pass
        with open(bp, "rb") as fh:
            body = fh.read()
        return status, body, ctype


def _request(path: str, params: dict | None = None) -> tuple[int, bytes, str]:
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            body = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, body, ctype
    except urllib.error.HTTPError as e:
        raise PlaybookError(
            f"Playbook API GET {path} failed: HTTP {e.code}"
            + (f" ({e.reason})" if getattr(e, "reason", None) else "")
        ) from None
    except (http.client.IncompleteRead, http.client.RemoteDisconnected):
        # Truncated through the egress proxy under urllib -- curl gets it
        # intact. Fall back before calling it a failure.
        return _curl_get(url)
    except urllib.error.URLError as e:
        if isinstance(
            e.reason, (http.client.IncompleteRead, http.client.RemoteDisconnected)
        ):
            return _curl_get(url)
        raise PlaybookError(
            f"Playbook API unreachable at {API_BASE}{path}: {e.reason}"
        ) from None
    except (TimeoutError, OSError) as e:
        raise PlaybookError(
            f"Playbook API request to {API_BASE}{path} failed: {e}"
        ) from None


def _http_get_json(path: str, params: dict | None = None) -> dict:
    status, body, ctype = _request(path, params)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PlaybookError(
            f"Playbook API GET {path} returned HTTP {status} with a "
            f"non-JSON body (expected JSON)"
        ) from None
    if not isinstance(data, dict):
        raise PlaybookError(
            f"Playbook API GET {path} returned an unexpected JSON shape"
        )
    return data


def _http_get_text(path: str, params: dict | None = None) -> str:
    status, body, _ctype = _request(path, params)
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise PlaybookError(
            f"Playbook API GET {path} returned HTTP {status} with "
            f"non-UTF-8 bytes (expected text)"
        ) from None


# ---------------------------------------------------------------------------
# Signature verification (fail closed). Mirrors install.sh exactly.
# ---------------------------------------------------------------------------
def _verify_signature(
    slug: str, version: str, skill_md: str, signature: str, signer_pubkey: str
) -> None:
    """Raise PlaybookError unless the ed25519 signature verifies. No return."""
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:
        raise PlaybookError(
            "PyNaCl is not installed (pip install pynacl). Refusing to "
            "install without signature verification."
        ) from None

    missing = [
        name
        for name, val in (
            ("skill_md", skill_md),
            ("signature", signature),
            ("signer_pubkey", signer_pubkey),
            ("version", version),
        )
        if not val
    ]
    if missing:
        raise PlaybookError(
            "Registry response is missing "
            + ", ".join(missing)
            + " -- refusing to install."
        )

    canonical = f"{slug}\n{version}\n{skill_md}".encode("utf-8")
    try:
        VerifyKey(bytes.fromhex(signer_pubkey.strip())).verify(
            canonical, base64.b64decode(signature.strip())
        )
    except (BadSignatureError, ValueError, binascii.Error):
        raise PlaybookError(
            "SIGNATURE VERIFICATION FAILED -- the package does not match "
            "the publisher's public key. Refusing to install."
        ) from None


def _safe_slug(slug: str) -> str:
    slug = (slug or "").strip()
    if not slug or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", slug):
        raise PlaybookError(
            f"Invalid skill slug {slug!r}: expected letters, digits, '-' or '_'"
        )
    return slug


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
def search_skills(
    query: str = "", category: str = "", sort: str = "newest", limit: int = 10
) -> dict:
    """Search the Playbook's free skill catalog.

    Args:
        query: keyword search across name/description ("" = everything).
        category: filter by category, e.g. "devtools", "writing", "media" ("" = all).
        sort: "newest" | "top" | "downloads" | "name".
        limit: how many results (1-50, default 10).

    Returns {"skills": [...], "count": n}. Each skill has slug, name,
    description, category, publisher (handle or null), latest_version,
    avg_stars, rating_count, downloads, signed (bool), created_at, updated_at.
    """
    sort = (sort or "newest").strip().lower()
    if sort not in ("newest", "top", "downloads", "name"):
        raise PlaybookError(
            f'Unknown sort {sort!r}: expected "newest", "top", "downloads" or "name"'
        )
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise PlaybookError(f"Invalid limit {limit!r}: expected an integer") from None
    limit = max(1, min(50, limit))

    params: dict = {"sort": sort, "limit": limit, "offset": 0}
    if query:
        params["q"] = query
    if category:
        params["category"] = category
    data = _http_get_json("/api/v1/skills", params)
    items = data.get("items") or []
    return {"skills": items, "count": len(items)}


@mcp.tool()
def get_skill(slug: str) -> dict:
    """Get a skill's full metadata plus a preview of its latest SKILL.md.

    Returns {"metadata": {...}, "skill_md": "...", "truncated": bool}.
    The SKILL.md preview is truncated to 6000 chars when longer (truncated=True).
    Use install_skill to get the complete, signature-verified file.
    """
    slug = _safe_slug(slug)
    meta = _http_get_json(f"/api/v1/skills/{urllib.parse.quote(slug)}")
    skill_md = _http_get_text(f"/api/v1/skills/{urllib.parse.quote(slug)}/skill.md")
    truncated = len(skill_md) > _SKILL_MD_TRUNCATE_AT
    if truncated:
        skill_md = (
            skill_md[:_SKILL_MD_TRUNCATE_AT]
            + f"\n\n[... truncated: preview limited to {_SKILL_MD_TRUNCATE_AT} "
            + "chars -- install the skill for the full SKILL.md]"
        )
    return {"metadata": meta, "skill_md": skill_md, "truncated": truncated}


@mcp.tool()
def install_skill(slug: str, version: str = "latest", dest_dir: str = "") -> dict:
    """Download a skill version, VERIFY its ed25519 signature, save SKILL.md.

    Fails closed: if PyNaCl is missing, the signature is invalid, or any
    required field is missing, NOTHING is written and a clear error is raised.

    Args:
        slug: skill slug, e.g. "regex-mastery".
        version: version string, or "latest" (default).
        dest_dir: where to install; default "./skills/<slug>/".
                  SKILL.md is written inside it (plus manifest.json when present).

    Returns {"path": ..., "version": ..., "verified": True, "note": ...}.
    NOTE: this direct version-JSON install does NOT increment the registry's
    download counter (bundle downloads do). To report it, POST
    {"slug": ..., "version": ...} to {API_BASE}/api/v1/installs -- see the
    returned note.
    """
    slug = _safe_slug(slug)
    version = (version or "latest").strip() or "latest"

    ver = _http_get_json(
        f"/api/v1/skills/{urllib.parse.quote(slug)}/versions/{urllib.parse.quote(version)}"
    )
    skill_md = ver.get("skill_md") or ""
    signature = ver.get("signature") or ""
    signer_pubkey = ver.get("signer_pubkey") or ""
    resolved_version = ver.get("version") or ""

    # Verify BEFORE touching the filesystem. Fail closed.
    _verify_signature(slug, resolved_version, skill_md, signature, signer_pubkey)

    dest = dest_dir.strip() if dest_dir else os.path.join(".", "skills", slug)
    os.makedirs(dest, exist_ok=True)
    md_path = os.path.join(dest, "SKILL.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(skill_md)
    if ver.get("manifest"):
        with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(ver["manifest"], fh, indent=2, default=str)

    return {
        "path": md_path,
        "version": resolved_version,
        "verified": True,
        "note": (
            "Signature verified against the publisher's key. This install did "
            "NOT increment the registry download counter (only bundle "
            "downloads count). To report it, POST "
            f'{{"slug": "{slug}", "version": "{resolved_version}"}} to '
            f"{API_BASE}/api/v1/installs."
        ),
    }


def _parse_since(since: str) -> str:
    """'24h' | '7d' | '30d' | ISO date -> ISO-8601 UTC string."""
    s = (since or "").strip().lower()
    now = _dt.datetime.now(_dt.timezone.utc)
    m = re.fullmatch(r"(\d+)\s*([hd])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "h":
            dt = now - _dt.timedelta(hours=n)
        else:
            dt = now - _dt.timedelta(days=n)
        return dt.isoformat()
    # ISO date / datetime string
    try:
        dt = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise PlaybookError(
            f'Invalid since {since!r}: expected "24h", "7d", "30d" '
            f'or an ISO-8601 date, e.g. "2026-09-14"'
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).isoformat()


@mcp.tool()
def whats_new(since: str = "7d") -> dict:
    """List skills approved recently, newest first.

    Args:
        since: "24h" | "7d" (default) | "30d" | an ISO-8601 date like "2026-09-14".

    Returns {"since": "<iso-8601 UTC>", "skills": [...], "count": n}.
    """
    iso = _parse_since(since)
    data = _http_get_json(
        "/api/v1/skills", {"since": iso, "sort": "newest", "limit": 20, "offset": 0}
    )
    items = data.get("items") or []
    return {"since": iso, "skills": items, "count": len(items)}


@mcp.tool()
def get_stats() -> dict:
    """Registry totals: skill_count, total_downloads, publisher_count, categories.

    Returns {"skill_count": int, "total_downloads": int, "publisher_count": int,
    "categories": [{"category": str, "count": int}, ...]}.
    """
    return _http_get_json("/api/v1/stats")


if __name__ == "__main__":
    if not _MCP_OK:
        print(
            'ERROR: the "mcp" package is not installed. '
            'Install it with: pip install "mcp"',
            file=sys.stderr,
        )
        sys.exit(1)
    mcp.run()
