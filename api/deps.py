"""Shared FastAPI dependencies: DB pool access and auth.

Lives in its own module so routers can import it without creating a
circular import through api.main.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from api.rate_limit import check_account_budget
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


async def account_write_budget(request: Request, account=Depends(current_account)) -> None:
    """Per-account rate budget on authenticated writes (HTTP 429).

    The anonymous per-IP middleware in api.rate_limit runs before auth and
    never sees these endpoints, so a stolen or abused API key had no rate
    budget at all. This dependency runs after current_account (FastAPI
    caches the sub-dependency, so there is no extra DB lookup) and charges
    one hit per account against api.rate_limit.ACCOUNT_BUCKETS. Budgets are
    generous for legitimate use and bite only scripts: over budget raises
    429 + Retry-After before the route handler runs.
    """
    retry_after = check_account_budget(
        str(account["id"]), request.method, request.url.path
    )
    if retry_after > 0:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded: slow down and retry.",
            headers={"Retry-After": str(int(retry_after))},
        )
