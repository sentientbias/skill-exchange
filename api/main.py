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
from .routers import accounts, bundles, feed, moderation, publish, ratings, skills


@asynccontextmanager
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
    msg = str(exc)
    code = status.HTTP_404_NOT_FOUND if msg.startswith("no ") else status.HTTP_400_BAD_REQUEST
    return JSONResponse(status_code=code, content={"detail": msg})


# ---------------------------------------------------------------------------
# auth dependencies live in api.deps (imported here for backwards compat)
# ---------------------------------------------------------------------------
from .deps import current_account, get_db, moderator  # noqa: F401,E402


@app.get("/api/v1/health", tags=["meta"])
async def health():
    return {"ok": True, "service": "skill-exchange", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# Branded front door (human visitors). All /api/* routes are untouched.
# ---------------------------------------------------------------------------
_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Skill Exchange — the free skill library for AI agents</title>
<meta name="description" content="Skill Exchange: a free, open, moderated registry of reusable skills for AI agents. Every skill Ed25519-signed.">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Skill Exchange">
<meta property="og:title" content="Skill Exchange — the free skill library for AI agents">
<meta property="og:description" content="A free, open, moderated registry of reusable skills for AI agents. Every skill Ed25519-signed by its publisher and human-moderated. Free forever.">
<meta property="og:url" content="https://skill-exchange-api-hoev.onrender.com/">
<meta property="og:image" content="https://x402-seller-a5et.onrender.com/static/brand/preview.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Skill Exchange — the free skill library for AI agents">
<meta name="twitter:description" content="A free, open, moderated registry of reusable skills for AI agents. Every skill Ed25519-signed. Free forever.">
<meta name="twitter:image" content="https://x402-seller-a5et.onrender.com/static/brand/preview.jpg">
<link rel="icon" type="image/png" href="https://x402-seller-a5et.onrender.com/static/brand/logo.png">
<style>
:root{--navy:#081426;--aqua:#22d3ee;--ink:#0f172a;--muted:#475569;--line:#e2e8f0}
*{box-sizing:border-box}
body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);line-height:1.65;-webkit-font-smoothing:antialiased}
.hero{background:linear-gradient(180deg,rgba(8,20,38,.66) 0%,rgba(8,20,38,.92) 100%),url('https://x402-seller-a5et.onrender.com/static/brand/hero.jpg') center 32%/cover no-repeat,var(--navy);color:#e2e8f0;padding:90px 24px 80px;text-align:center}
.hero img{width:96px;height:96px;border-radius:20px;box-shadow:0 12px 40px rgba(34,211,238,.35)}
.hero h1{color:#fff;font-size:clamp(30px,5vw,48px);letter-spacing:-.03em;margin:22px 0 10px}
.hero h1 span{color:var(--aqua)}
.hero p{max-width:36em;margin:0 auto 30px;color:#cbd5e1;font-size:17px}
.btn{display:inline-block;padding:13px 26px;border-radius:10px;font-weight:700;font-size:15px;margin:6px;border:1px solid transparent}
.btn-p{background:linear-gradient(135deg,#2563eb,#0891b2);color:#fff;text-decoration:none}
.btn-g{border-color:#475569;color:#e2e8f0;text-decoration:none}
.row{max-width:900px;margin:0 auto;padding:56px 24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:18px}
.card{border:1px solid var(--line);border-radius:14px;padding:24px}
.card h3{margin:0 0 8px;font-size:17px}
.card p{margin:0;color:var(--muted);font-size:14.5px}
.card a{color:#0e7490;font-weight:700}
footer{text-align:center;color:#94a3b8;font-size:13px;padding:0 24px 40px}
code{background:#f1f5f9;padding:1px 7px;border-radius:6px;font-size:13px}
</style>
</head>
<body>
<div class="hero">
  <img src="https://x402-seller-a5et.onrender.com/static/brand/logo.png" alt="Skill Exchange logo">
  <h1>Skill <span>Exchange</span></h1>
  <p>The free, open registry of reusable skills for AI agents. Every skill Ed25519-signed by its publisher and human-moderated. Free forever.</p>
  <a class="btn btn-p" href="https://x402-seller-a5et.onrender.com/">Visit the Exchange</a>
  <a class="btn btn-g" href="/api/v1/skills?limit=50">Catalog API</a>
</div>
<div class="row">
  <div class="card"><h3>For humans</h3><p>Browse the full library, publishing guide, and the paid <b>Exchange Pro</b> lane at the main site.</p><p><a href="https://x402-seller-a5et.onrender.com/">x402-seller-a5et.onrender.com &rarr;</a></p></div>
  <div class="card"><h3>For agents</h3><p>This is the machine API. Fetch <code>/api/v1/skills</code> for the catalog, <code>/api/v1/bundles/&lt;slug&gt;</code> for signed downloads, <code>/feed.xml</code> for new-skill RSS.</p></div>
  <div class="card"><h3>Publish</h3><p>Create a publisher account, Ed25519-sign your SKILL.md, submit for moderation. Three steps, documented on the main site.</p><p><a href="https://x402-seller-a5et.onrender.com/#publish">How to publish &rarr;</a></p></div>
</div>
<footer>Skill Exchange — free, open, moderated. Exchange Pro — pay-per-call on Base.</footer>
</body>
</html>
"""


@app.get("/", include_in_schema=False)
async def index():
    """Branded front door for human visitors. Machine API lives under /api/*."""
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_INDEX_HTML)


app.include_router(feed.router)
app.include_router(skills.router, prefix="/api/v1")
app.include_router(bundles.router, prefix="/api/v1")
app.include_router(publish.router, prefix="/api/v1")
app.include_router(ratings.router, prefix="/api/v1")
app.include_router(accounts.router, prefix="/api/v1")
app.include_router(moderation.router, prefix="/api/v1")
