"""Accounts and API keys."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import current_account, get_db
from api.schemas import AccountCreate, AccountOut, KeyCreate, KeyOut
from core import store

router = APIRouter(tags=["accounts"])


@router.post("/accounts", response_model=AccountOut,
             status_code=status.HTTP_201_CREATED)
async def create_account(body: AccountCreate, pool=Depends(get_db)):
    """Register a publisher account. The response includes the API key in
    PLAINTEXT -- save it now, it is never shown again."""
    account = await store.create_account(
        pool, body.handle, body.display_name)
    return AccountOut(
        id=str(account["id"]),
        handle=account["handle"],
        display_name=account["display_name"],
        is_moderator=account["is_moderator"],
        created_at=account["created_at"],
        api_key=account["api_key"],
    )


@router.get("/accounts/me")
async def me(account=Depends(current_account)):
    return {
        "id": str(account["id"]),
        "handle": account["handle"],
        "display_name": account["display_name"],
        "is_moderator": account["is_moderator"],
        "created_at": account["created_at"],
    }


@router.post("/accounts/me/keys", response_model=KeyOut,
             status_code=status.HTTP_201_CREATED)
async def create_key(
    body: KeyCreate, account=Depends(current_account), pool=Depends(get_db)
):
    """Mint an extra API key. Plaintext shown once."""
    key = await store.create_api_key(pool, str(account["id"]), body.name)
    return KeyOut(
        id=str(key["id"]),
        key_prefix=key["key_prefix"],
        name=key["name"],
        created_at=key["created_at"],
        api_key=key["api_key"],
    )


@router.get("/accounts/me/keys")
async def list_keys(account=Depends(current_account), pool=Depends(get_db)):
    keys = await store.list_api_keys(pool, str(account["id"]))
    return {"items": keys}


@router.delete("/accounts/me/keys/{key_id}", status_code=status.HTTP_200_OK)
async def revoke_key(
    key_id: str, account=Depends(current_account), pool=Depends(get_db)
):
    ok = await store.revoke_api_key(pool, str(account["id"]), key_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such key")
    return {"ok": True, "revoked": key_id}
