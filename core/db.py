"""Async Postgres connection pool (asyncpg).

Works against any Postgres, including Supabase (use the connection-pooling
URL on port 6543 or the direct URL on 5432).
"""
from __future__ import annotations

import json
import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Decode jsonb columns as Python dicts instead of raw strings."""
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v),
        decoder=lambda v: json.loads(v),
        schema="pg_catalog",
        format="text",
    )


async def get_pool() -> asyncpg.Pool:
    """Lazily create (and cache) the global connection pool."""
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=10, init=_init_connection
        )
    return _pool


async def close_pool() -> None:
    """Close the global pool (call on app shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
