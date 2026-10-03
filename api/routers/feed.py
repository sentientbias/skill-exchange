"""Public RSS 2.0 feed of newly published skills.

GET /feed.xml — the 20 most recently approved skills, hand-rolled XML,
no extra dependencies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

import hashlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from api.deps import get_db
from api.routers.skills import _if_none_match_matches
from core import store

router = APIRouter(tags=["feed"])

BASE_URL = "https://skill-exchange-api-hoev.onrender.com"


def _rfc2822(value) -> str:
    """Coerce a DB timestamp into an RFC 2822 pubDate string."""
    dt: datetime
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


@router.get("/feed.xml")
async def rss_feed(request: Request, pool=Depends(get_db)):
    skills = await store.list_skills(pool, sort="newest", limit=20)
    items: list[str] = []
    for s in skills:
        slug = escape(str(s.get("slug", "")))
        title = escape(str(s.get("name") or s.get("slug", "")))
        desc = escape(str(s.get("description") or ""))
        version = escape(str(s.get("latest_version") or ""))
        link = f"{BASE_URL}/api/v1/skills/{slug}"
        items.append(
            "    <item>\n"
            f"      <title>{title}</title>\n"
            f"      <link>{link}</link>\n"
            f"      <description>{desc}</description>\n"
            f"      <guid isPermaLink=\"false\">skill:{slug}:{version}</guid>\n"
            f"      <pubDate>{_rfc2822(s.get('created_at'))}</pubDate>\n"
            "    </item>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        "    <title>The Playbook — New Skills</title>\n"
        f"    <link>{BASE_URL}</link>\n"
        "    <description>The newest skills published on The Playbook, "
        "the free skill exchange for AI agents.</description>\n"
        "    <language>en-us</language>\n"
        + "\n".join(items)
        + "\n  </channel>\n"
        "</rss>\n"
    )
    # Strong ETag over the exact rendered bytes, same freshness protocol
    # as the skill.md readers and bundle downloads: feed pollers asking
    # "did anything new publish?" get a 304 instead of a full re-parse.
    # The feed changes only when skills publish, so a short 5-minute
    # cache window is honest; per-IP budgets (60/60s) already apply.
    etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'
    cache_control = "public, max-age=300"
    if _if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304,
                        headers={"ETag": etag,
                                 "Cache-Control": cache_control})
    return Response(content=body, media_type="application/rss+xml",
                    headers={"ETag": etag, "Cache-Control": cache_control})


SITEMAP_MAX_URLS = 5000


def _iso_date(value):
    """Coerce a DB timestamp into a YYYY-MM-DD sitemap <lastmod> string.

    Returns None when the value is missing or unparseable, in which case
    the <url> entry is emitted without <lastmod> rather than with a lie.
    """
    dt: datetime
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return dt.date().isoformat()


@router.get("/sitemap.xml")
async def sitemap(request: Request, pool=Depends(get_db)):
    """Public sitemap.xml: every approved skill's human-readable page.

    Search engines and discovery crawlers (npm, PyPI, and Hugging Face all
    publish one) can find the skill pages without hammering /browse or the
    list API page by page. Emits the front door, /browse, and up to
    SITEMAP_MAX_URLS approved skill pages; nothing about the page renders
    changes, this is a new machine-readable surface only.
    """
    urls: list[str] = [
        f"    <url><loc>{BASE_URL}/</loc></url>",
        f"    <url><loc>{BASE_URL}/browse</loc></url>",
    ]
    # store.list_skills clamps each request to 100 rows (per-request cost
    # bound), so walk offset pages until the page comes back short or the
    # sitemap cap is reached.
    offset = 0
    while len(urls) - 2 < SITEMAP_MAX_URLS:
        page = await store.list_skills(pool, sort="newest", limit=100,
                                       offset=offset)
        if not page:
            break
        for s in page:
            slug = escape(str(s.get("slug", "")))
            lastmod = _iso_date(s.get("updated_at"))
            lastmod_tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
            urls.append(
                f"    <url><loc>{BASE_URL}/skills/{slug}</loc>{lastmod_tag}</url>"
            )
        if len(page) < 100:
            break
        offset += 100
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    # Same freshness protocol as the RSS feed: strong ETag over the exact
    # rendered bytes, 304 on match. The sitemap changes only when skills
    # publish or update, so a one-hour cache window is honest; the per-IP
    # budget (60/60s, same as the feed) bounds crawler re-fetch volume.
    etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'
    cache_control = "public, max-age=3600"
    if _if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304,
                        headers={"ETag": etag,
                                 "Cache-Control": cache_control})
    return Response(content=body, media_type="application/xml",
                    headers={"ETag": etag, "Cache-Control": cache_control})


@router.get("/robots.txt")
async def robots_txt():
    """Static robots.txt pointing crawlers at the sitemap.

    Pure static bytes, no DB work; a generous per-IP budget (120/60s)
    keeps pathological re-fetch loops cheap for everyone.
    """
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=86400"})
