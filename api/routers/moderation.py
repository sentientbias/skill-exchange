"""Moderation queue (moderators only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import get_db, moderator
from api.schemas import ModerateIn
from core import store

router = APIRouter(tags=["moderation"])


@router.get("/moderation/queue")
async def queue(
    status: str = Query(default="pending"),
    account=Depends(moderator),
    pool=Depends(get_db),
):
    return {
        "items": await store.moderation_queue(pool, status=status),
        "status": status,
    }


@router.post("/moderation/queue/{queue_id}/decide")
async def decide(
    queue_id: str,
    body: ModerateIn,
    account=Depends(moderator),
    pool=Depends(get_db),
):
    """Approve or reject a queued submission. Approving a new skill makes it
    public; approving a new version promotes it to latest.

    Approving a referred publisher's FIRST skill automatically converts their
    referral and issues the referrer a pro pass (see ``referral`` in the
    response)."""
    return await store.decide_moderation(
        pool, str(account["id"]), queue_id, body.approve, body.note
    )


@router.post("/moderation/skills/{slug}/delist")
async def delist_skill(
    slug: str,
    account=Depends(moderator),
    pool=Depends(get_db),
):
    """Delist a live skill (moderators only): pulls it from the public
    catalog, detail pages, skill.md, and bundles. Used for Pro-lane
    products that must not be freely downloadable."""
    try:
        return await store.delist_skill(pool, str(account["id"]), slug)
    except ValueError as e:
        from fastapi import HTTPException, status as http_status

        raise HTTPException(http_status.HTTP_404_NOT_FOUND, str(e))
