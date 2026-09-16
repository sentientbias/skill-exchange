"""Ratings and install events."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import current_account, get_db
from api.schemas import InstallIn, RatingIn
from core import store

router = APIRouter(tags=["ratings"])


@router.post("/skills/{slug}/ratings", status_code=status.HTTP_201_CREATED)
async def rate_skill(
    slug: str,
    body: RatingIn,
    account=Depends(current_account),
    pool=Depends(get_db),
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
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"no version '{body.version}' of '{body.slug}'",
        )
    await store.record_install(pool, str(ver["id"]), None, body.client)
    return {"ok": True, "slug": body.slug, "version": ver["version"]}
