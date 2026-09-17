"""The Playbook MCP server (the free skill exchange for AI agents).

Exposes the registry as tools an agent can call mid-task:

    search_skills  - find skills by keyword/category
    get_skill      - metadata, versions, ratings for one skill
    install_skill  - fetch a skill from the registry over HTTPS, verify its
                     ed25519 signature client-side, save SKILL.md locally,
                     and report the install (feeds the download counter)
    publish_skill  - publish a new signed skill (needs your API key)
    rate_skill     - rate a skill 1-5 (needs your API key)

Run with:  python -m mcp_server.server
Point it at the same DATABASE_URL as the REST API.

Claude Desktop config snippet:

    {
      "mcpServers": {
        "skill-exchange": {
          "command": "python",
          "args": ["-m", "mcp_server.server"],
          "cwd": "/path/to/skill-exchange",
          "env": {"DATABASE_URL": "postgresql://..."}
        }
      }
    }
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Allow running as `python -m mcp_server.server` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from mcp.server.fastmcp import FastMCP  # noqa: E402  (mcp<2)
except ImportError:  # mcp>=2 renamed FastMCP -> MCPServer
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP  # noqa: E402
    except ImportError:
        # mcp not installed (e.g. test envs): keep tool functions importable
        # as plain callables so the install logic stays testable.
        class FastMCP:  # noqa: E402
            def __init__(self, name: str):
                self.name = name

            def tool(self):
                def deco(fn):
                    return fn
                return deco

            def run(self):
                raise RuntimeError(
                    "the 'mcp' package is not installed; "
                    "pip install 'mcp>=1.4' to serve this over MCP")

from core import db, signing, store  # noqa: E402
from core.auth import extract_bearer  # noqa: E402

mcp = FastMCP("skill-exchange")


def _jsonable(obj):
    """Convert asyncpg Records / UUIDs / datetimes into plain JSON-safe data."""
    return json.loads(json.dumps(obj, default=str))


# ---------------------------------------------------------------------------
# Client-side installer: fetch -> verify -> save -> report.
#
# The registry's download counter only moves when a client POSTs to
# /api/v1/installs, so installing through this tool (rather than just
# reading the raw SKILL.md URL) is what makes "downloads" real. Signature
# verification is MANDATORY and client-side: a skill is executable
# instructions, so we never save bytes whose signature does not check out
# (see SECURITY.md). Fail closed on any verification problem.
# ---------------------------------------------------------------------------

REGISTRY_BASE = os.environ.get(
    "SKILL_EXCHANGE_API", "https://skill-exchange-api-hoev.onrender.com"
).rstrip("/")
INSTALL_CLIENT = "mcp-installer/1.0"
DEFAULT_SKILLS_DIR = os.path.expanduser("~/workspace/skills/installed")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
_HTTP_UA = {"User-Agent": "skill-exchange-mcp-installer/1.0"}


def _http_json(method: str, url: str, payload: dict | None = None,
             timeout: int = 60) -> dict:
    """GET/POST JSON with retries, then a curl fallback.

    The registry runs on Render's free tier behind a proxy that
    intermittently truncates larger bodies for Python urllib while curl
    sails through, so: try urllib a few times with backoff, then fall back
    to curl via subprocess before giving up.
    """
    data = None
    headers = dict(_HTTP_UA)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    last_err: Exception | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers=headers,
                                         method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - transient net flakiness
            last_err = exc
            time.sleep(2 * (attempt + 1))

    # curl fallback
    try:
        cmd = ["curl", "-sS", "-f", "-A", _HTTP_UA["User-Agent"],
               "--max-time", str(timeout), "-X", method, url]
        if payload is not None:
            cmd += ["-H", "Content-Type: application/json",
                    "-d", json.dumps(payload)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        if out.returncode != 0:
            err = out.stderr.strip()[:200]
            if out.returncode == 22 and "404" in err:
                raise ValueError(f"registry returned 404 for {url}")
            raise ValueError(f"curl failed: {err}")
        return json.loads(out.stdout)
    except FileNotFoundError:
        pass  # no curl either; report the original urllib error below
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"registry request failed ({method} {url}): {exc}") from exc

    raise ValueError(
        f"registry request failed ({method} {url}): {last_err}")


def _api_get(api_base: str, path: str) -> dict:
    try:
        return _http_json("GET", api_base + path)
    except ValueError as exc:
        msg = str(exc)
        if "404" in msg:
            raise ValueError(f"registry returned 404 for {path}") from exc
        raise


def _api_post_install(api_base: str, slug: str, version: str) -> dict:
    return _http_json("POST", api_base + "/api/v1/installs",
                      {"slug": slug, "version": version,
                       "client": INSTALL_CLIENT})


async def _install_skill_impl(
    slug: str,
    version: str = "latest",
    dest_dir: str = "",
    api_base: str = "",
) -> dict:
    """Full client-side install flow. Returns a success/failure summary dict."""
    slug = (slug or "").strip().lower()
    if not _SLUG_RE.match(slug):
        return {"error": "slug must match ^[a-z0-9][a-z0-9_-]{1,40}$",
                "installed": False}
    version = (version or "latest").strip()
    base = (api_base or REGISTRY_BASE).rstrip("/")

    # 1. Fetch the signed package from the registry.
    try:
        ver = _api_get(base, f"/api/v1/skills/{slug}/versions/{version}")
    except ValueError as exc:
        return {"error": str(exc), "installed": False}
    skill_md = ver.get("skill_md") or ""
    signature = ver.get("signature") or ""
    public_key = ver.get("signer_pubkey") or ""
    real_version = ver.get("version") or version
    if not skill_md or not signature or not public_key:
        return {"error": "registry response missing skill_md/signature/"
                         "signer_pubkey -- refusing to install",
                "installed": False}

    # 2. Verify the ed25519 signature client-side. FAIL CLOSED.
    try:
        ok = signing.verify_package(
            slug, real_version, skill_md, signature, public_key)
    except Exception as exc:  # never let a verify crash become an install
        return {"error": f"signature verification errored ({exc}) -- "
                         "refusing to install",
                "installed": False}
    if not ok:
        return {"error": "SIGNATURE VERIFICATION FAILED -- the package does "
                         "not match the publisher's public key. Refusing to "
                         "install. Do not bypass this.",
                "installed": False,
                "slug": slug, "version": real_version}

    # 3. Save SKILL.md (+ manifest) to the local skills directory.
    root = os.path.expanduser(dest_dir) if dest_dir else DEFAULT_SKILLS_DIR
    skill_dir = os.path.join(root, slug)
    try:
        os.makedirs(skill_dir, exist_ok=True)
        md_path = os.path.join(skill_dir, "SKILL.md")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(skill_md)
        manifest = ver.get("manifest")
        if manifest:
            with open(os.path.join(skill_dir, "manifest.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, default=str)
    except OSError as exc:
        return {"error": f"could not write to {skill_dir}: {exc}",
                "installed": False}

    # 4. Report the install so the registry's download counter moves.
    try:
        report = _api_post_install(base, slug, real_version)
    except ValueError as exc:
        return {"installed": True, "slug": slug, "version": real_version,
                "path": md_path, "signature": "verified",
                "warning": f"saved locally, but install report failed: {exc}. "
                           "The download counter did not increment."}
    reported = bool(report.get("ok"))

    return {
        "installed": True,
        "slug": slug,
        "version": real_version,
        "path": md_path,
        "signature": "verified",
        "reported": reported,
        "summary": (f"installed {slug} v{real_version} -> {md_path}; "
                    f"signature verified against publisher key; "
                    f"download counter {'incremented' if reported else 'NOT updated'}"),
    }


@mcp.tool()
async def search_skills(
    query: str = "",
    category: str = "",
    sort: str = "top",
    limit: int = 10,
) -> dict:
    """Search the skill registry. Returns matching skills with ratings and
    download counts. Sort: newest | top | downloads | name."""
    pool = await db.get_pool()
    items = await store.list_skills(
        pool, q=query, category=category, sort=sort, limit=limit, offset=0
    )
    return {"skills": _jsonable(items)}


@mcp.tool()
async def get_skill(slug: str) -> dict:
    """Get full metadata for one skill: description, versions, ratings."""
    pool = await db.get_pool()
    skill = await store.get_skill(pool, slug)
    if skill is None:
        return {"error": f"no approved skill '{slug}'"}
    return {"skill": _jsonable(skill)}


@mcp.tool()
async def install_skill(
    slug: str,
    version: str = "latest",
    dest_dir: str = "",
    api_base: str = "",
) -> dict:
    """Install a skill from the registry: fetch the signed package over
    HTTPS, verify the ed25519 signature client-side, save SKILL.md locally,
    and report the install so the registry's download counter increments.

    Verification is mandatory: the tool REFUSES to install when the
    signature does not verify against the publisher's public key.
    Canonical bytes are utf8(slug + '\\n' + version + '\\n' + skill_md).
    dest_dir defaults to ~/workspace/skills/installed/<slug>/.
    """
    return await _install_skill_impl(slug, version, dest_dir, api_base)


@mcp.tool()
async def publish_skill(
    api_key: str,
    name: str,
    slug: str,
    description: str,
    skill_md: str,
    signature: str,
    public_key: str,
    category: str = "general",
    version: str = "1.0.0",
) -> dict:
    """Publish a new skill. The skill_md must ALREADY be signed locally with
    your private ed25519 key (see scripts/keygen.py and SECURITY.md); the
    server re-verifies the signature before queueing for moderation."""
    pool = await db.get_pool()
    token = extract_bearer(f"Bearer {api_key}") or api_key
    account = await store.get_account_by_key(pool, token)
    if account is None:
        return {"error": "invalid API key"}
    try:
        out = await store.create_skill(
            pool,
            str(account["id"]),
            name=name,
            slug=slug,
            description=description,
            category=category,
            version=version,
            skill_md=skill_md,
            manifest={
                "name": name, "slug": slug, "version": version,
                "description": description, "category": category,
            },
            signature=signature,
            public_key=public_key,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"published": _jsonable(out)}


@mcp.tool()
async def rate_skill(
    api_key: str, slug: str, stars: int, comment: str = ""
) -> dict:
    """Rate an installed skill 1-5 stars with an optional comment."""
    pool = await db.get_pool()
    token = extract_bearer(f"Bearer {api_key}") or api_key
    account = await store.get_account_by_key(pool, token)
    if account is None:
        return {"error": "invalid API key"}
    try:
        rating = await store.rate_skill(
            pool, str(account["id"]), slug, stars, comment)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"rated": _jsonable(rating)}


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("error: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)
    mcp.run()


if __name__ == "__main__":
    main()
