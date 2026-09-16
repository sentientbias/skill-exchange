"""Storage layer: all SQL lives here.

Both the REST API and the MCP server call these functions, so behavior is
identical across interfaces. Every function takes an asyncpg pool (or
connection) explicitly -- no globals, easy to test.
"""
from __future__ import annotations

import os
import re
from typing import Any

import asyncpg

from . import auth, signing

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,30}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_CATEGORIES = {
    "research", "media", "3d", "automation", "meta",
    "devtools", "writing", "data", "general",
}
_SORTS = {
    "newest": "s.created_at DESC",
    "top": "rt.avg_stars DESC NULLS LAST, rt.rating_count DESC",
    "downloads": "dl.total_downloads DESC NULLS LAST",
    "name": "s.name ASC",
}


def _check_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug or ""):
        raise ValueError("slug must match ^[a-z0-9][a-z0-9_-]{1,40}$")


def _check_handle(handle: str) -> None:
    if not _HANDLE_RE.match(handle or ""):
        raise ValueError("handle must match ^[a-z0-9][a-z0-9_-]{1,30}$")


def _check_version(version: str) -> None:
    if not _VERSION_RE.match(version or ""):
        raise ValueError("version must be semver like 1.2.3")


def _check_category(category: str) -> None:
    if category not in _CATEGORIES:
        raise ValueError(f"category must be one of {sorted(_CATEGORIES)}")


def _auto_approve() -> bool:
    return os.environ.get("AUTO_APPROVE", "false").lower() in ("1", "true", "yes")


def _d(row: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# accounts & api keys
# ---------------------------------------------------------------------------

async def create_account(
    db: asyncpg.Pool,
    handle: str,
    display_name: str,
    *,
    is_moderator: bool = False,
) -> dict[str, Any]:
    """Create an account and mint its first API key.

    Returns the account plus ``api_key`` in PLAINTEXT -- show it to the user
    once and never store it.
    """
    _check_handle(handle)
    name = (display_name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("display_name must be 1-80 chars")
    full_key, key_hash, key_prefix = auth.new_api_key()
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    """insert into accounts (handle, display_name, is_moderator)
                       values ($1, $2, $3) returning *""",
                    handle, name, is_moderator,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ValueError(f"handle '{handle}' is taken") from exc
            await conn.execute(
                    """insert into api_keys (account_id, key_hash, key_prefix, name)
                       values ($1, $2, $3, 'default')""",
                    row["id"], key_hash, key_prefix,
                )
    account = _d(row)
    account["api_key"] = full_key
    return account


async def get_account_by_key(
    db: asyncpg.Pool, full_key: str
) -> dict[str, Any] | None:
    """Look up the account owning an API key. Returns None if unknown/revoked."""
    key_hash = auth.hash_key(full_key)
    row = await db.fetchrow(
        """select a.* from accounts a
           join api_keys k on k.account_id = a.id
           where k.key_hash = $1 and k.revoked = false""",
        key_hash,
    )
    return _d(row)


async def touch_key(db: asyncpg.Pool, full_key: str) -> None:
    await db.execute(
        "update api_keys set last_used_at = now() where key_hash = $1",
        auth.hash_key(full_key),
    )


async def create_api_key(
    db: asyncpg.Pool, account_id: str, name: str
) -> dict[str, Any]:
    """Mint an additional key. Returns row plus plaintext ``api_key`` (show once)."""
    label = (name or "default").strip()[:40] or "default"
    full_key, key_hash, key_prefix = auth.new_api_key()
    row = await db.fetchrow(
        """insert into api_keys (account_id, key_hash, key_prefix, name)
           values ($1, $2, $3, $4)
           returning id, key_prefix, name, created_at""",
        account_id, key_hash, key_prefix, label,
    )
    out = _d(row)
    out["api_key"] = full_key
    return out


async def list_api_keys(db: asyncpg.Pool, account_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch(
        """select id, key_prefix, name, created_at, last_used_at, revoked
           from api_keys where account_id = $1 order by created_at""",
        account_id,
    )
    return [_d(r) for r in rows]


async def revoke_api_key(
    db: asyncpg.Pool, account_id: str, key_id: str
) -> bool:
    res = await db.execute(
        """update api_keys set revoked = true
           where id = $1::uuid and account_id = $2::uuid and revoked = false""",
        key_id, account_id,
    )
    return res.endswith("1")


# ---------------------------------------------------------------------------
# skills: reads
# ---------------------------------------------------------------------------

_LIST_SELECT = """
select s.id, s.slug, s.name, s.description, s.category, s.status,
       s.created_at, s.updated_at,
       lv.version as latest_version,
       coalesce(rt.avg_stars, 0)::float as avg_stars,
       coalesce(rt.rating_count, 0)::int as rating_count,
       coalesce(dl.total_downloads, 0)::int as downloads
from skills s
left join skill_versions lv on lv.id = s.latest_version_id
left join (select skill_id, avg(stars) as avg_stars, count(*) as rating_count
           from ratings group by skill_id) rt on rt.skill_id = s.id
left join (select skill_id, sum(downloads) as total_downloads
           from skill_versions group by skill_id) dl on dl.skill_id = s.id
"""


async def list_skills(
    db: asyncpg.Pool,
    q: str = "",
    category: str = "",
    sort: str = "newest",
    limit: int = 20,
    offset: int = 0,
    *,
    include_pending: bool = False,
) -> list[dict[str, Any]]:
    """List skills. Public callers see only approved skills."""
    order = _SORTS.get(sort, _SORTS["newest"])
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    status_filter = "" if include_pending else "and s.status = 'approved'"
    query = (
        _LIST_SELECT
        + f"where ($1 = '' or s.slug ilike '%' || $1 || '%' "
        + " or s.name ilike '%' || $1 || '%' "
        + " or s.description ilike '%' || $1 || '%') "
        + "and ($2 = '' or s.category = $2) "
        + status_filter
        + f" order by {order} limit $3 offset $4"
    )
    rows = await db.fetch(query, q or "", category or "", limit, offset)
    return [_d(r) for r in rows]


async def get_skill(
    db: asyncpg.Pool, slug: str, *, include_pending: bool = False
) -> dict[str, Any] | None:
    """Full skill detail: metadata, all versions, recent ratings."""
    _check_slug(slug)
    status_filter = "" if include_pending else "and s.status = 'approved'"
    row = await db.fetchrow(
        _LIST_SELECT + f"where s.slug = $1 {status_filter}", slug
    )
    if row is None:
        return None
    skill = _d(row)
    versions = await db.fetch(
        """select id, version, manifest, signature, signer_pubkey,
                  downloads, created_at
           from skill_versions where skill_id = $1 order by created_at""",
        skill["id"],
    )
    skill["versions"] = [_d(v) for v in versions]
    ratings = await db.fetch(
        """select r.stars, r.comment, r.created_at, a.handle
           from ratings r join accounts a on a.id = r.account_id
           where r.skill_id = $1 order by r.created_at desc limit 20""",
        skill["id"],
    )
    skill["recent_ratings"] = [_d(r) for r in ratings]
    return skill


async def get_version(
    db: asyncpg.Pool,
    slug: str,
    version: str | None = None,
    *,
    include_pending: bool = False,
) -> dict[str, Any] | None:
    """One version, INCLUDING the full SKILL.md content and signature.

    version=None resolves to the latest approved version.
    """
    _check_slug(slug)
    status_filter = "" if include_pending else "and s.status = 'approved'"
    if version is None or version == "latest":
        row = await db.fetchrow(
            f"""select v.*, s.slug from skill_versions v
                join skills s on s.id = v.skill_id
                where s.slug = $1 and v.id = s.latest_version_id {status_filter}""",
            slug,
        )
    else:
        _check_version(version)
        row = await db.fetchrow(
            f"""select v.*, s.slug from skill_versions v
                join skills s on s.id = v.skill_id
                where s.slug = $1 and v.version = $2 {status_filter}""",
            slug, version,
        )
    return _d(row)


# ---------------------------------------------------------------------------
# skills: publishing
# ---------------------------------------------------------------------------

async def create_skill(
    db: asyncpg.Pool,
    account_id: str,
    *,
    name: str,
    slug: str,
    description: str,
    category: str,
    version: str,
    skill_md: str,
    manifest: dict[str, Any],
    signature: str,
    public_key: str,
) -> dict[str, Any]:
    """Publish a brand-new skill. Goes to the moderation queue unless
    AUTO_APPROVE is set. The signature is verified BEFORE anything is stored,
    which also proves the submitter holds the private key."""
    _check_slug(slug)
    _check_version(version)
    _check_category(category)
    name = (name or "").strip()
    description = (description or "").strip()
    skill_md = skill_md or ""
    if not name or len(name) > 120:
        raise ValueError("name must be 1-120 chars")
    if not description or len(description) > 2000:
        raise ValueError("description must be 1-2000 chars")
    if len(skill_md) < 50 or len(skill_md) > 200_000:
        raise ValueError("skill_md must be 50-200000 chars")
    if not signing.verify_package(slug, version, skill_md, signature, public_key):
        raise ValueError("signature verification failed -- check key and content")
    approved = _auto_approve()
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                skill = await conn.fetchrow(
                    """insert into skills
                       (slug, name, description, category, author_account_id, status)
                       values ($1, $2, $3, $4, $5::uuid, $6)
                       returning *""",
                    slug, name, description, category, account_id,
                    "approved" if approved else "pending",
                )
            except asyncpg.UniqueViolationError as exc:
                raise ValueError(f"slug '{slug}' is taken") from exc
            ver = await conn.fetchrow(
                """insert into skill_versions
                   (skill_id, version, skill_md, manifest, signature, signer_pubkey)
                   values ($1, $2, $3, $4::jsonb, $5, $6) returning *""",
                skill["id"], version, skill_md,
                __import__("json").dumps(manifest or {}), signature, public_key,
            )
            await conn.execute(
                "update skills set latest_version_id = $1 where id = $2",
                ver["id"], skill["id"],
            )
            if not approved:
                await conn.execute(
                    """insert into moderation_queue
                       (kind, skill_id, skill_version_id, submitted_by)
                       values ('new_skill', $1, $2, $3::uuid)""",
                    skill["id"], ver["id"], account_id,
                )
    out = _d(skill)
    out["status"] = "approved" if approved else "pending"
    out["version_id"] = str(ver["id"])
    return out


async def create_version(
    db: asyncpg.Pool,
    account_id: str,
    slug: str,
    *,
    version: str,
    skill_md: str,
    manifest: dict[str, Any],
    signature: str,
    public_key: str,
) -> dict[str, Any]:
    """Publish a new version of an existing skill. Only the original author
    (or a moderator) may do this. Verified + queued like a new skill."""
    _check_slug(slug)
    _check_version(version)
    skill_md = skill_md or ""
    if len(skill_md) < 50 or len(skill_md) > 200_000:
        raise ValueError("skill_md must be 50-200000 chars")
    if not signing.verify_package(slug, version, skill_md, signature, public_key):
        raise ValueError("signature verification failed -- check key and content")
    approved = _auto_approve()
    async with db.acquire() as conn:
        async with conn.transaction():
            skill = await conn.fetchrow(
                "select * from skills where slug = $1", slug
            )
            if skill is None:
                raise ValueError(f"no such skill '{slug}'")
            author = await conn.fetchval(
                "select is_moderator from accounts where id = $1::uuid", account_id
            )
            if str(skill["author_account_id"]) != str(account_id) and not author:
                raise ValueError("only the original author can publish new versions")
            try:
                ver = await conn.fetchrow(
                    """insert into skill_versions
                       (skill_id, version, skill_md, manifest, signature, signer_pubkey)
                       values ($1, $2, $3, $4::jsonb, $5, $6) returning *""",
                    skill["id"], version, skill_md,
                    __import__("json").dumps(manifest or {}), signature, public_key,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ValueError(
                    f"version {version} already exists for '{slug}'") from exc
            if approved:
                await conn.execute(
                    """update skills set latest_version_id = $1,
                       updated_at = now() where id = $2""",
                    ver["id"], skill["id"],
                )
            else:
                await conn.execute(
                    """insert into moderation_queue
                       (kind, skill_id, skill_version_id, submitted_by)
                       values ('new_version', $1, $2, $3::uuid)""",
                    skill["id"], ver["id"], account_id,
                )
    out = _d(ver)
    out["approved"] = approved
    return out


# ---------------------------------------------------------------------------
# ratings
# ---------------------------------------------------------------------------

async def rate_skill(
    db: asyncpg.Pool,
    account_id: str,
    slug: str,
    stars: int,
    comment: str = "",
) -> dict[str, Any]:
    """Rate a skill (1-5). One rating per account per skill; re-rating updates."""
    _check_slug(slug)
    stars = int(stars)
    if stars < 1 or stars > 5:
        raise ValueError("stars must be 1-5")
    comment = (comment or "").strip()[:2000]
    skill = await db.fetchrow(
        "select id from skills where slug = $1 and status = 'approved'", slug
    )
    if skill is None:
        raise ValueError(f"no approved skill '{slug}'")
    row = await db.fetchrow(
        """insert into ratings (skill_id, account_id, stars, comment)
           values ($1, $2::uuid, $3, $4)
           on conflict (skill_id, account_id)
           do update set stars = excluded.stars, comment = excluded.comment,
                         updated_at = now()
           returning *""",
        skill["id"], account_id, stars, comment,
    )
    return _d(row)


# ---------------------------------------------------------------------------
# installs
# ---------------------------------------------------------------------------

async def record_install(
    db: asyncpg.Pool,
    version_id: str,
    account_id: str | None,
    client: str,
) -> None:
    """Log an install event and bump the version's download counter."""
    await db.execute(
        """insert into install_events (skill_version_id, account_id, client)
           values ($1::uuid, $2::uuid, $3)""",
        version_id, account_id, (client or "unknown")[:80],
    )
    await db.execute(
        "update skill_versions set downloads = downloads + 1 where id = $1::uuid",
        version_id,
    )


# ---------------------------------------------------------------------------
# moderation
# ---------------------------------------------------------------------------

async def moderation_queue(
    db: asyncpg.Pool, status: str = "pending"
) -> list[dict[str, Any]]:
    if status not in ("pending", "approved", "rejected"):
        raise ValueError("bad status")
    rows = await db.fetch(
        """select q.*, s.slug, s.name, v.version, a.handle as submitted_by_handle
           from moderation_queue q
           join skills s on s.id = q.skill_id
           left join skill_versions v on v.id = q.skill_version_id
           join accounts a on a.id = q.submitted_by
           where q.status = $1 order by q.created_at""",
        status,
    )
    return [_d(r) for r in rows]


async def decide_moderation(
    db: asyncpg.Pool,
    reviewer_account_id: str,
    queue_id: str,
    approve: bool,
    note: str = "",
) -> dict[str, Any]:
    """Approve or reject a queued submission. Moderators only (checked by caller)."""
    async with db.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                """select * from moderation_queue
                   where id = $1::uuid and status = 'pending'""",
                queue_id,
            )
            if item is None:
                raise ValueError("queue item not found or already decided")
            decision = "approved" if approve else "rejected"
            if approve:
                if item["kind"] == "new_skill":
                    await conn.execute(
                        "update skills set status = 'approved' where id = $1",
                        item["skill_id"],
                    )
                else:  # new_version -> promote to latest
                    await conn.execute(
                        """update skills set latest_version_id = $1,
                           updated_at = now() where id = $2""",
                        item["skill_version_id"], item["skill_id"],
                    )
            else:
                if item["kind"] == "new_skill":
                    await conn.execute(
                        "update skills set status = 'rejected' where id = $1",
                        item["skill_id"],
                    )
                # rejected versions simply never become latest; row stays for audit
            await conn.execute(
                """update moderation_queue
                   set status = $2, reviewer_account_id = $3::uuid,
                       note = $4, decided_at = now()
                   where id = $1::uuid""",
                queue_id, decision, reviewer_account_id, (note or "")[:2000],
            )
    return {"id": queue_id, "decision": decision}
