"""Shared FastAPI dependencies: DB pool access and auth.

Lives in its own module so routers can import it without creating a
circular import through api.main.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from core import db, store
from core.auth import extract_bearer


async def get_db():
    return await db.get_pool()


async def current_account(request: Request, pool=Depends(get_db)) -> dict:
    token = extract_bearer(request.headers.get("authorization"))
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    account = await store.get_account_by_key(pool, token)
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked key")
    await store.touch_key(pool, token)
    return account


async def moderator(request: Request, pool=Depends(get_db)) -> dict:
    account = await current_account(request, pool)
    if not account.get("is_moderator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "moderator only")
    return account
