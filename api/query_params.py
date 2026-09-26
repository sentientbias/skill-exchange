"""Shared query-parameter validators for the API routers.

Lives in its own module so routers can import it without creating a
circular import through each other (api.routers.skills and
api.routers.bundles both need the `since` gate).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, status


def validate_since(since: str) -> str:
    """ISO-8601 gate for the `since` filter. Raises 422 on garbage so a
    typo never silently returns the unfiltered catalog."""
    try:
        datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "since must be an ISO-8601 timestamp, e.g. 2026-09-14T00:00:00Z",
        )
    return since
