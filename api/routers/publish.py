"""Publishing: new skills and new versions (authenticated, signed, queued)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from api.deps import current_account, get_db
from api.schemas import SkillPublish, VersionPublish
from core import store

router = APIRouter(tags=["publish"])


@router.post("/skills", status_code=status.HTTP_202_ACCEPTED)
async def publish_skill(
    body: SkillPublish,
    account=Depends(current_account),
    pool=Depends(get_db),
):
    """Publish a new skill. Signature is verified server-side, then the skill
    enters the moderation queue (unless AUTO_APPROVE=true)."""
    return await store.create_skill(
        pool,
        str(account["id"]),
        name=body.name,
        slug=body.slug,
        description=body.description,
        category=body.category,
        version=body.version,
        skill_md=body.skill_md,
        manifest=body.manifest,
        signature=body.signature,
        public_key=body.public_key,
    )


@router.post("/skills/{slug}/versions", status_code=status.HTTP_202_ACCEPTED)
async def publish_version(
    slug: str,
    body: VersionPublish,
    account=Depends(current_account),
    pool=Depends(get_db),
):
    """Publish a new version of your own skill. Same verify + queue flow."""
    return await store.create_version(
        pool,
        str(account["id"]),
        slug,
        version=body.version,
        skill_md=body.skill_md,
        manifest=body.manifest,
        signature=body.signature,
        public_key=body.public_key,
    )
