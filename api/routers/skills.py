"""Public skill browsing: list, search, detail, version download."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

from api.deps import get_db
from core import store

router = APIRouter(tags=["skills"])


def _validate_since(since: str) -> str:
    """ISO-8601 gate for the `since` filter. Raises 422 on garbage so a
    typo never silently returns the unfiltered catalog."""
    try:
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "since must be an ISO-8601 timestamp, e.g. 2026-09-14T00:00:00Z",
        )
    return since


@router.get("/skills")
async def list_skills(
    q: str = Query(default="", description="Search name/description/slug"),
    category: str = Query(default=""),
    sort: str = Query(default="newest",
                      description="newest | top | downloads | name"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    since: str = Query(default="",
                       description="ISO-8601: only skills updated after this"),
    pool=Depends(get_db),
):
    if since:
        _validate_since(since)
    return {
        "items": await store.list_skills(
            pool, q=q, category=category, sort=sort, limit=limit,
            offset=offset, since=since,
        ),
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats")
async def stats(pool=Depends(get_db)):
    """Public catalog stats for agents and front-ends.

    Totals plus the per-category breakdown — one call gives a client
    everything for filter pills and hero numbers.
    """
    return await store.public_stats(pool)


@router.get("/skills/{slug}")
async def get_skill(slug: str, pool=Depends(get_db)):
    skill = await store.get_skill(pool, slug)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no skill '{slug}'")
    return skill


@router.get("/skills/{slug}/versions/{version}")
async def get_version(slug: str, version: str, pool=Depends(get_db)):
    """Download one version: full SKILL.md + signature + public key.

    Verify client-side with core.signing.verify_package() (or any ed25519
    implementation) over canonical bytes: slug + "\\n" + version + "\\n" + skill_md.
    """
    ver = await store.get_version(pool, slug, version)
    if ver is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"no version '{version}' of '{slug}'")
    ver["verify"] = {
        "algorithm": "ed25519",
        "canonical_format": "utf8(slug + '\\n' + version + '\\n' + skill_md)",
    }
    return ver


@router.get("/skills/{slug}/skill.md", response_class=PlainTextResponse)
async def read_skill_md(slug: str, pool=Depends(get_db)):
    """Front-door reader: the latest approved SKILL.md as raw markdown.

    Open this URL in a browser to read a skill solo — no JSON parsing.
    """
    ver = await store.get_version(pool, slug, None)
    if ver is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no skill '{slug}'")
    return PlainTextResponse(
        ver["skill_md"],
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{slug}-latest.md"',
        },
    )


@router.get("/skills/{slug}/versions/{version}/skill.md",
            response_class=PlainTextResponse)
async def read_version_skill_md(slug: str, version: str, pool=Depends(get_db)):
    """Raw SKILL.md of one specific version, as markdown."""
    ver = await store.get_version(pool, slug, version)
    if ver is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"no version '{version}' of '{slug}'")
    return PlainTextResponse(
        ver["skill_md"],
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{slug}-{version}.md"',
        },
    )
