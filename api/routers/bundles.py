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
from api.routers.skills import _raise_unknown_skill, _raise_unknown_version
from core import store

router = APIRouter(tags=["bundles"])


def _receipt(slug: str, ver: dict) -> dict:
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
    manifest = ver.get("manifest") or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{slug}/SKILL.md", ver["skill_md"])
        zf.writestr(
            f"{slug}/manifest.json",
            json.dumps(manifest, indent=2) + "\n",
        )
        zf.writestr(
            f"{slug}/receipt.json", json.dumps(_receipt(slug, ver), indent=2) + "\n"
        )
    buf.seek(0)
    return buf.read()


@router.get("/bundles")
async def list_bundles(
    request: Request,
    q: str = Query(default="", description="Search name/description/slug"),
    category: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    pool=Depends(get_db),
):
    """List install bundles for every approved skill (latest version each)."""
    skills = await store.list_skills(
        pool, q=q, category=category, sort="name", limit=limit, offset=offset
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
    return {"items": items, "limit": limit, "offset": offset}


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
    filename = f"{slug}-{ver['version']}.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
