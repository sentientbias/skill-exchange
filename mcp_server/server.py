"""The Playbook MCP server (the free skill exchange for AI agents).

Exposes the registry as tools an agent can call mid-task:

    search_skills  - find skills by keyword/category
    get_skill      - metadata, versions, ratings for one skill
    install_skill  - download the full signed package (verifies server-side
                     data, returns everything needed for client-side verify)
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
import sys

# Allow running as `python -m mcp_server.server` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from mcp.server.fastmcp import FastMCP  # noqa: E402  (mcp<2)
except ImportError:  # mcp>=2 renamed FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP  # noqa: E402

from core import db, signing, store  # noqa: E402
from core.auth import extract_bearer  # noqa: E402

mcp = FastMCP("skill-exchange")


def _jsonable(obj):
    """Convert asyncpg Records / UUIDs / datetimes into plain JSON-safe data."""
    return json.loads(json.dumps(obj, default=str))


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
    api_key: str = "",
) -> dict:
    """Install a skill: returns the complete signed package.

    VERIFY BEFORE USE: recompute
        canonical = slug + "\\n" + version + "\\n" + skill_md   (utf-8)
    and check the ed25519 signature against public_key. Never install a
    package whose signature does not verify -- see SECURITY.md.
    """
    pool = await db.get_pool()
    ver = await store.get_version(pool, slug, version)
    if ver is None:
        return {"error": f"no version '{version}' of '{slug}'"}

    account_id = None
    if api_key:
        token = extract_bearer(f"Bearer {api_key}") or api_key
        account = await store.get_account_by_key(pool, token)
        if account:
            account_id = str(account["id"])

    await store.record_install(pool, str(ver["id"]), account_id, client="mcp")

    return {
        "slug": ver["slug"],
        "version": ver["version"],
        "manifest": _jsonable(ver["manifest"]),
        "skill_md": ver["skill_md"],
        "signature": ver["signature"],
        "public_key": ver["signer_pubkey"],
        "signature_algorithm": signing.SIGNATURE_ALGORITHM,
        "canonical_format": "utf8(slug + '\\n' + version + '\\n' + skill_md)",
        "install_hint": (
            "Save skill_md as SKILL.md inside <skills-dir>/<slug>/ "
            "and add the manifest as skill.yaml for tooling."
        ),
    }


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
