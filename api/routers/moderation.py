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
    public; approving a new version promotes it to latest."""
    return await store.decide_moderation(
        pool, str(account["id"]), queue_id, body.approve, body.note
    )
