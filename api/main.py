"""Skill Exchange REST API.

Run locally:  uvicorn api.main:app --reload
Docs:         http://localhost:8000/docs  (OpenAPI/Swagger)
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core import db
from .deps import get_db  # noqa: F401  (re-exported for routers)
from .routers import accounts, moderation, publish, ratings, skills


async def lifespan(app: FastAPI):
    await db.get_pool()  # fail fast if DATABASE_URL is wrong
    yield
    await db.close_pool()


app = FastAPI(
    title="Skill Exchange API",
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


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError):
    ...
