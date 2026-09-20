"""Storage layer: all SQL lives here.

Both the REST API and the MCP server call these functions, so behavior is
identical across interfaces. Every function takes an asyncpg pool (or
connection) explicitly -- no globals, easy to test.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import asyncpg

from . import auth, propass, signing

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
    referred_by: str | None = None,
) -> dict[str, Any]:
    """Create an account and mint its first API key.

    Returns the account plus ``api_key`` in PLAINTEXT -- show it to the user
    once and never store it.

    ``referred_by`` (optional) is the handle of the existing publisher who
    referred this account. It must name a real, different account; a pending
    referral row is recorded and converts to a pro pass for the referrer when
    this account's first skill is approved.
    """
    _check_handle(handle)
    name = (display_name or "").strip()
    if not name or len(name) > 80:
        raise ValueError("display_name must be 1-80 chars")
    ref = (referred_by or "").strip().lower() or None
    if ref:
        _check_handle(ref)
        if ref == handle:
            raise ValueError("you cannot refer yourself")
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
            if ref:
                referrer = await conn.fetchrow(
                    "select id from accounts where handle = $1", ref
                )
                if referrer is None:
                    raise ValueError(f"referrer handle '{ref}' does not exist")
                try:
                    await conn.execute(
                        """insert into referrals
                           (referrer_account_id, referred_account_id)
                           values ($1, $2)""",
                        referrer["id"], row["id"],
                    )
                    await conn.execute(
                        "update accounts set referred_by_handle = $1 where id = $2",
                        ref, row["id"],
                    )
                except asyncpg.UndefinedTableError:
                    # migration 002 not applied yet: the account is still
                    # created; the referral is dropped rather than failing
                    # signup. Re-apply once migrated (or re-register).
                    logging.warning(
                        "referrals table missing (migration 002 pending); "
                        "dropping referral %s -> %s", ref, handle,
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


async def catalog_stats(db: asyncpg.Pool) -> dict[str, int]:
    """Front-door numbers: approved skills and total recorded installs.

    Kept as one cheap aggregate query; the front door calls it once per
    page view and degrades silently if the DB is unreachable.
    """
    row = await db.fetchrow(
        """select count(*)::int as skill_count,
                  coalesce(sum(dl.total_downloads), 0)::int as total_downloads
           from skills s
           left join (select skill_id, sum(downloads) as total_downloads
                      from skill_versions group by skill_id) dl
             on dl.skill_id = s.id
           where s.status = 'approved'"""
    )
    d = _d(row) or {}
    return {
        "skill_count": int(d.get("skill_count") or 0),
        "total_downloads": int(d.get("total_downloads") or 0),
    }


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
    if approved:
        # AUTO_APPROVE path: still convert referrals (normally this happens
        # in moderation decide).
        out["referral"] = await maybe_convert_referral(db, account_id)
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
    (or a moderator) may do this. Verified + queued like a new skill. The
    signing key must match a key the skill has used before (key continuity),
    unless the submitter is a moderator rotating a lost key."""
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
            is_moderator = await conn.fetchval(
                "select is_moderator from accounts where id = $1::uuid", account_id
            )
            if str(skill["author_account_id"]) != str(account_id) and not is_moderator:
                raise ValueError("only the original author can publish new versions")
            # Signing-key continuity: a new version must be signed with a key
            # the skill has used before. Without this, a stolen API key lets an
            # attacker silently swap the signing identity -- the signature still
            # verifies (against the attacker's own key) and only a human
            # comparing hex strings would notice. Moderators may still rotate a
            # lost key through the documented out-of-band flow.
            prior = await conn.fetch(
                "select distinct signer_pubkey from skill_versions"
                " where skill_id = $1",
                skill["id"],
            )
            known_keys = {
                str(row["signer_pubkey"]).strip().lower() for row in prior
            }
            if (known_keys
                    and public_key.strip().lower() not in known_keys
                    and not is_moderator):
                raise ValueError(
                    "signing key changed: new versions must be signed with the "
                    "same key as previous versions. If you lost your key, ask "
                    "a moderator to rotate it for you."
                )
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
        """select q.*, s.slug, s.name, s.description, v.version, v.skill_md,
                  v.manifest, v.signature, v.signer_pubkey,
                  a.handle as submitted_by_handle
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
    result = {
        "id": queue_id,
        "decision": decision,
        "submitted_by": str(item["submitted_by"]),
    }
    if approve:
        # Automatic conversion: a referred publisher's first approved skill
        # earns their referrer a pro pass. Never blocks the approval itself.
        result["referral"] = await maybe_convert_referral(
            db, str(item["submitted_by"])
        )
    return result


# ---------------------------------------------------------------------------
# referrals & pro passes
# ---------------------------------------------------------------------------

async def maybe_convert_referral(
    db: asyncpg.Pool, referred_account_id: str
) -> dict[str, Any]:
    """Convert a pending referral when the referred publisher's first skill is
    approved, minting a pro pass for the referrer.

    Idempotent: a referral converts at most once (status flips to 'converted'
    inside the same transaction that issues the pass). Safe to call after any
    skill approval; it no-ops when there is no pending referral or when the
    account still has no approved skills.

    If PROPASS_SIGNING_KEY is not configured, the referral stays pending (a
    later approval or a manual re-run will convert it) -- approval itself is
    never blocked by pass issuance.
    """
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                ref = await conn.fetchrow(
                    """select r.*, a.handle as referrer_handle
                       from referrals r
                       join accounts a on a.id = r.referrer_account_id
                       where r.referred_account_id = $1::uuid
                         and r.status = 'pending'""",
                    referred_account_id,
                )
            except asyncpg.UndefinedTableError:
                # migration 002 not applied yet: no-op rather than breaking
                # moderation.
                logging.warning(
                    "referrals table missing (migration 002 pending); "
                    "skipping referral conversion")
                return {"converted": False, "reason": "referral tables not migrated"}
            if ref is None:
                return {"converted": False, "reason": "no pending referral"}
            approved_count = await conn.fetchval(
                """select count(*) from skills
                   where author_account_id = $1::uuid and status = 'approved'""",
                referred_account_id,
            )
            if not approved_count:
                return {"converted": False, "reason": "no approved skills yet"}
            signing_key = os.environ.get("PROPASS_SIGNING_KEY", "").strip()
            if not signing_key:
                logging.warning(
                    "PROPASS_SIGNING_KEY not configured; referral %s stays "
                    "pending (approval unaffected)", ref["id"],
                )
                return {
                    "converted": False,
                    "reason": "pro-pass signing key not configured",
                }
            token, pass_id, exp_epoch = propass.mint_pass(
                ref["referrer_handle"], signing_key
            )
            expires_at = datetime.fromtimestamp(exp_epoch, tz=timezone.utc)
            await conn.execute(
                """insert into pro_passes
                   (account_id, referral_id, pass_id, token, expires_at)
                   values ($1::uuid, $2::uuid, $3, $4, $5)""",
                ref["referrer_account_id"], ref["id"], pass_id, token,
                expires_at,
            )
            await conn.execute(
                """update referrals set status = 'converted', converted_at = now()
                   where id = $1::uuid""",
                ref["id"],
            )
            return {
                "converted": True,
                "referrer": ref["referrer_handle"],
                "pass_id": pass_id,
            }


async def list_referrals(db: asyncpg.Pool) -> list[dict[str, Any]]:
    """Operator view: every referral with referrer/referred handles,
    conversion state, and the issued pass (if any)."""
    rows = await db.fetch(
        """select r.id, r.status, r.created_at, r.converted_at,
                  fr.handle as referrer_handle,
                  fa.handle as referred_handle,
                  p.pass_id, p.expires_at
           from referrals r
           join accounts fr on fr.id = r.referrer_account_id
           join accounts fa on fa.id = r.referred_account_id
           left join pro_passes p on p.referral_id = r.id
           order by r.created_at desc"""
    )
    return [_d(r) for r in rows]


async def list_pro_passes(
    db: asyncpg.Pool, account_id: str
) -> list[dict[str, Any]]:
    """Pro passes earned by one account (the referrer retrieves their own
    tokens here)."""
    rows = await db.fetch(
        """select pass_id, token, issued_at, expires_at
           from pro_passes
           where account_id = $1::uuid
           order by issued_at desc""",
        account_id,
    )
    return [_d(r) for r in rows]
