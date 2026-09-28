"""Install bundles: downloadable zip packages of approved skills.

Each bundle is a zip containing:
  {slug}/SKILL.md      — the raw skill playbook (exact signed bytes)
  {slug}/manifest.json — the skill manifest
  {slug}/receipt.json  — signature, public key, and verify instructions

Verify client-side with core.signing.verify_package() (or any ed25519
implementation) over canonical bytes: slug + "\\n" + version + "\\n" + skill_md.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from api.deps import get_db
from api.query_params import validate_since
from api.routers.skills import _raise_unknown_skill, _raise_unknown_version
from core import store

router = APIRouter(tags=["bundles"])


def _receipt(slug: str, ver: dict) -> dict:
    # Defense in depth: the slug that goes into filenames and zip entries
    # is the DB-canonical slug from the version row, never the raw request
    # path parameter. Today's strict slug regex (^([a-z0-9][a-z0-9_-]{1,40})$)
    # in store._check_slug makes header injection / path escape through the
    # request slug unexploitable (get_version validates before any response
    # is built), but the routers should not rely on the store's regex: if
    # the slug alphabet ever widens (or a new route forgets the check), a
    # crafted slug containing `"` / `;` / CRLF would land in the
    # Content-Disposition filename and the zip entry paths. `ver["slug"]`
    # comes from `select v.*, s.slug` -- the canonical registry slug.
    slug = ver.get("slug") or slug
    return {
        "slug": slug,
        "version": ver["version"],
        "signature": ver["signature"],
        "public_key": ver.get("signer_pubkey"),
        "verify": {
            "algorithm": "ed25519",
            "canonical_format": "utf8(slug + '\\n' + version + '\\n' + skill_md)",
            "steps": [
                "recompute canonical bytes: "
                "utf8(slug + '\\n' + version + '\\n' + SKILL.md)",
                "ed25519-verify(signature, canonical_bytes, public_key)",
            ],
        },
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_zip(slug: str, ver: dict) -> bytes:
    # store._d() normalizes the manifest to a dict at the DB boundary, so by
    # the time we get here it is always a JSON object (never a double-encoded
    # string). The `or {}` stays as cheap insurance for hand-built dicts.
    # Zip entry paths use the DB-canonical slug (see _receipt for the
    # rationale): zip entries land on the client's filesystem, so their
    # directory names must not be built from request input.
    canon = ver.get("slug") or slug
    manifest = ver.get("manifest") or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{canon}/SKILL.md", ver["skill_md"])
        zf.writestr(
            f"{canon}/manifest.json",
            json.dumps(manifest, indent=2) + "\n",
        )
        zf.writestr(
            f"{canon}/receipt.json", json.dumps(_receipt(slug, ver), indent=2) + "\n"
        )
    buf.seek(0)
    return buf.read()


@router.get("/bundles")
async def list_bundles(
    request: Request,
    # Same search-text length bound as /api/v1/skills (see that endpoint's
    # comment): q feeds three leading-wildcard ILIKE matches per row, so an
    # unbounded q turned a cheap unauthenticated list read into an expensive
    # full-table pattern scan with no rate budget.
    q: str = Query(default="", max_length=200,
                   description="Search name/description/slug"),
    category: str = Query(default="", max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    # Same deep-offset guard as the /skills list endpoint (see its comment):
    # unauthenticated GET reads have no rate budget, so cap the offset.
    offset: int = Query(default=0, ge=0, le=10000),
    since: str = Query(default="",
                       description="ISO-8601: only bundles updated after this"),
    pool=Depends(get_db),
):
    """List install bundles for every approved skill (latest version each).

    `since` mirrors the /skills list filter: an agent update loop can poll
    `?since=<last seen>` for bundles newer than its last check instead of
    diffing the whole catalog.
    """
    if since:
        validate_since(since)
    skills = await store.list_skills(
        pool, q=q, category=category, sort="name", limit=limit, offset=offset,
        since=since,
    )
    base = str(request.base_url).rstrip("/")
    items = []
    for s in skills:
        version = s.get("latest_version")
        if not version:
            continue
        items.append(
            {
                "slug": s["slug"],
                "name": s["name"],
                "version": version,
                "download_url": f"{base}/api/v1/bundles/{s['slug']}",
                "skill_md_url": f"{base}/api/v1/skills/{s['slug']}/skill.md",
            }
        )
    # Same page-independent match total as /api/v1/skills: a bundle update
    # loop needs to know how many results exist to page through them with
    # limit/offset. NOTE: count_skills counts matching skills with or
    # without a published version; items above skips versionless skills,
    # so total can run ahead of len(items) by the versionless count
    # (near-zero in practice).
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": await store.count_skills(
            pool, q=q, category=category, since=since,
        ),
    }


@router.get("/bundles/{slug}")
async def download_bundle(
    slug: str,
    version: str | None = Query(default=None,
                                description="Pin a version; default latest"),
    pool=Depends(get_db),
):
    """Download one skill as a zip bundle (SKILL.md + manifest + receipt)."""
    ver = await store.get_version(pool, slug, version)
    if ver is None:
        skill = await store.get_skill(pool, slug)
        if skill is None:
            await _raise_unknown_skill(slug, pool)
        await _raise_unknown_version(slug, version or "latest", pool)
    data = _build_zip(slug, ver)
    # Canonical slug for the download filename: ver["slug"] is the
    # DB-canonical registry slug; the request slug is only a lookup key.
    # Same defense-in-depth rationale as _receipt -- the filename reaches a
    # header, so it must not be built from request input.
    canon = ver.get("slug") or slug
    filename = f"{canon}-{ver['version']}.zip"
    # Cache policy, npm-tarball convention: a published version row is
    # content-stable (UNIQUE version per skill, no UPDATE path on
    # skill_versions, the ed25519 signature covers slug+version+skill_md),
    # so a pinned-version bundle stays valid forever and is safe to cache
    # for a year, marked immutable. (The zip's receipt.json carries a
    # request-time fetched_at and zip metadata embeds build timestamps, so
    # bytes can vary slightly per request; the signed content does not.)
    # An unpinned "latest" download resolves at request time and flips on
    # the next publish, so it gets a short 5-minute public cache only.
    # Headers only: no page, copy, API shape, or Pro-tier change.
    cache_control = (
        "public, max-age=31536000, immutable" if version
        else "public, max-age=300"
    )
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": cache_control,
        },
    )
