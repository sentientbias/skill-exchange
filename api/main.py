"""Skill Exchange REST API.

Run locally:  uvicorn api.main:app --reload
Docs:         http://localhost:8000/docs  (OpenAPI/Swagger)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import db, store
from core.build_info import build_info

from api.front_door import front_door_html
from api.skill_page import skill_not_found_html, skill_page_html

from .deps import get_db  # noqa: F401  (re-exported for routers)
from .query_guard import RejectUnknownQueryParamsMiddleware
from .request_size_guard import RequestSizeGuardMiddleware
from .routers import accounts, bundles, feed, moderation, publish, ratings, skills

import logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_pool()  # fail fast if DATABASE_URL is wrong
    yield
    await db.close_pool()


app = FastAPI(
    title="The Playbook — Skill Exchange API",
    description=(
        "Registry for portable agent skills: publish signed skill packages, "
        "search them, install them, rate them."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fail loudly on unknown query params (e.g. ?pack=paid): without this,
# FastAPI silently drops undeclared params and returns *unfiltered* data.
app.add_middleware(RejectUnknownQueryParamsMiddleware)

# Reject oversized request bodies (413) before FastAPI parses them into
# memory: several write endpoints are anonymous or cheaply reachable, and
# the bodies were unbounded. The cap is generous (1 MiB) next to the largest
# legit payload (~250 KB for a max-size SKILL.md publish).
app.add_middleware(RequestSizeGuardMiddleware)

# Local brand assets (self-contained; the free service's face must not depend
# on the paid service's uptime). Served from api/static, shipped in the image.
app.mount("/static", StaticFiles(directory="api/static"), name="static")


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError):
    msg = str(exc)
    code = status.HTTP_404_NOT_FOUND if msg.startswith("no ") else status.HTTP_400_BAD_REQUEST
    return JSONResponse(status_code=code, content={"detail": msg})


# ---------------------------------------------------------------------------
# auth dependencies live in api.deps (imported here for backwards compat)
# ---------------------------------------------------------------------------
from .deps import current_account, get_db, moderator  # noqa: F401,E402


@app.get("/api/v1/health", tags=["meta"])
async def health():
    # `build` is additive: existing fields are untouched so old consumers
    # keep working. Lets operators confirm which commit is actually live.
    return {
        "ok": True,
        "service": "skill-exchange",
        "version": "1.0.0",
        "build": build_info(),
    }


# ---------------------------------------------------------------------------
# Branded front door (human visitors). All /api/* routes are untouched.
#
# Design: server-rendered from the live DB (newest + most-installed strips,
# live stats band) — modeled on registry homepages that show real catalog
# content (Hugging Face Hub, PyPI "trending projects"). No JS, so plain-HTTP
# agent clients read the same content. Degrades to the static hero on any
# DB error; the front door used to be fully static and must never 500.
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def index(pool=Depends(get_db)):
    """Branded front door: hero + live catalog strips. API under /api/*."""
    try:
        latest = await store.list_skills(pool, sort="newest", limit=6)
        top = await store.list_skills(pool, sort="downloads", limit=6)
        stats = await store.catalog_stats(pool)
    except Exception:
        log.warning("front door: DB unavailable, serving static fallback",
                    exc_info=True)
        latest = top = stats = None
    return HTMLResponse(front_door_html(latest, top, stats))


@app.get("/skills/{slug}", include_in_schema=False)
async def skill_detail(slug: str, pool=Depends(get_db)):
    """Per-skill detail page: install command, versions, signature, ratings.

    Human-readable counterpart to GET /api/v1/skills/{slug} (the npm/PyPI
    package-page slot). Only approved skills get pages; unknown slugs,
    invalid slugs, and DB outages all render the 404 page -- this route
    must never 500, same as the front door.
    """
    try:
        skill = await store.get_skill(pool, slug)
    except Exception:
        log.warning("skill page: lookup failed for %r", slug, exc_info=True)
        skill = None
    if skill is None:
        return HTMLResponse(skill_not_found_html(slug), status_code=404)
    return HTMLResponse(skill_page_html(skill))


app.include_router(feed.router)
app.include_router(skills.router, prefix="/api/v1")
app.include_router(bundles.router, prefix="/api/v1")
app.include_router(publish.router, prefix="/api/v1")
app.include_router(ratings.router, prefix="/api/v1")
app.include_router(accounts.router, prefix="/api/v1")
app.include_router(moderation.router, prefix="/api/v1")
