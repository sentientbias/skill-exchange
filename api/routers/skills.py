"""Public skill browsing: list, search, detail, version download."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

from api.deps import get_db
from api.query_params import validate_since
from core import store

router = APIRouter(tags=["skills"])


async def _raise_unknown_skill(slug: str, pool) -> None:
    """404 for an unknown slug that tells the client what DOES exist.

    The detail is a dict: a human-readable message (with inline
    'did you mean' hint when there is one) plus a machine-readable
    `suggestions` list so agents and install.sh can recover in one
    round trip instead of guessing.
    """
    suggestions = await store.suggest_slugs(pool, slug)
    message = f"no skill '{slug}'"
    if suggestions:
        quoted = ", ".join(f"'{s}'" for s in suggestions)
        message += f"; did you mean: {quoted}?"
    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        {"message": message, "suggestions": suggestions},
    )


async def _raise_unknown_version(slug: str, version: str, pool) -> None:
    """404 for an unknown version that lists the versions that exist."""
    available = await store.list_version_labels(pool, slug)
    message = f"no version '{version}' of '{slug}'"
    if available:
        message += f"; available versions: {', '.join(available)}"
    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        {"message": message, "available_versions": available},
    )


@router.get("/skills")
async def list_skills(
    # Search text is length-bounded before it reaches Postgres (threat-8-adjacent
    # read amplification): q feeds three leading-wildcard ILIKE matches per
    # row, so a multi-KB q string turned a cheap list read into an expensive
    # full-table pattern scan on an unauthenticated endpoint with no rate
    # budget (a 4000-char q returned 200 on the live API, confirmed 2026-09-27).
    # 200 chars is generous for real searches; the /browse HTML page already
    # truncates q to 100, and categories are short slugs capped at 64.
    q: str = Query(default="", max_length=200,
                   description="Search name/description/slug"),
    category: str = Query(default="", max_length=64),
    sort: str = Query(default="newest",
                      description="newest | top | downloads | name"),
    limit: int = Query(default=20, ge=1, le=100),
    # Deep-offset scans make the database skip N rows before returning any;
    # an unbounded offset turns a cheap list read into a free-for-all
    # read-amplification probe (unauthenticated GETs have no rate budget).
    # 10k pages of 100 is two orders of magnitude past the catalog size and
    # the /browse UI clamps page internally, so no legitimate client hits it.
    offset: int = Query(default=0, ge=0, le=10000),
    since: str = Query(default="",
                       description="ISO-8601: only skills updated after this"),
    pool=Depends(get_db),
):
    if since:
        validate_since(since)
    return {
        "items": await store.list_skills(
            pool, q=q, category=category, sort=sort, limit=limit,
            offset=offset, since=since,
        ),
        "limit": limit,
        "offset": offset,
        "total": await store.count_skills(
            pool, q=q, category=category, since=since,
        ),
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
        await _raise_unknown_skill(slug, pool)
    return skill


@router.get("/skills/{slug}/versions/{version}")
async def get_version(slug: str, version: str, pool=Depends(get_db)):
    """Download one version: full SKILL.md + signature + public key.

    Verify client-side with core.signing.verify_package() (or any ed25519
    implementation) over canonical bytes: slug + "\\n" + version + "\\n" + skill_md.
    """
    ver = await store.get_version(pool, slug, version)
    if ver is None:
        skill = await store.get_skill(pool, slug)
        if skill is None:
            await _raise_unknown_skill(slug, pool)
        await _raise_unknown_version(slug, version, pool)
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
        await _raise_unknown_skill(slug, pool)
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
        skill = await store.get_skill(pool, slug)
        if skill is None:
            await _raise_unknown_skill(slug, pool)
        await _raise_unknown_version(slug, version, pool)
    return PlainTextResponse(
        ver["skill_md"],
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{slug}-{version}.md"',
        },
    )
