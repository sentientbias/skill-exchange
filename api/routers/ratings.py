"""Ratings and install events."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import account_write_budget, current_account, get_db
from api.schemas import InstallIn, RatingIn
from core import store

router = APIRouter(tags=["ratings"])


@router.post("/skills/{slug}/ratings", status_code=status.HTTP_201_CREATED)
async def rate_skill(
    slug: str,
    body: RatingIn,
    account=Depends(current_account),
    pool=Depends(get_db),
    budget=Depends(account_write_budget),
):
    """Rate a skill 1-5 stars. Re-rating updates your previous rating."""
    return await store.rate_skill(
        pool, str(account["id"]), slug, body.stars, body.comment
    )


@router.post("/installs", status_code=status.HTTP_201_CREATED)
async def record_install(body: InstallIn, pool=Depends(get_db)):
    """Log that a skill version was installed (feeds download counts).

    No auth required -- anonymous installs count too. Call this after a
    successful client-side signature verification.
    """
    ver = await store.get_version(pool, body.slug, body.version)
    if ver is None:
        # get_version returns None for two different problems and they need
        # different recoveries. A skill with at least one approved version
        # always has version labels, so an empty label list means the skill
        # itself is unknown: answer with the same did-you-mean shape as the
        # read endpoints instead of a misleading "no version" message.
        available = await store.list_version_labels(pool, body.slug)
        if not available:
            suggestions = await store.suggest_slugs(pool, body.slug)
            message = f"no skill '{body.slug}'"
            if suggestions:
                quoted = ", ".join(f"'{s}'" for s in suggestions)
                message += f"; did you mean: {quoted}?"
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                {"message": message, "suggestions": suggestions},
            )
        message = f"no version '{body.version}' of '{body.slug}'"
        if available:
            message += f"; available versions: {', '.join(available)}"
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"message": message, "available_versions": available},
        )
    await store.record_install(pool, str(ver["id"]), None, body.client)
    return {"ok": True, "slug": body.slug, "version": ver["version"]}
