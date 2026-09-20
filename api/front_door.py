"""The Playbook front door (GET /): branded HTML for human visitors.

Design: the best registries show real catalog content on the front page
(Hugging Face Hub lists models with like counts; PyPI ships a "trending
projects" section — pypi/warehouse#1837). So this page is server-rendered
from the live DB — no JS, so plain-HTTP agent clients read it too — with
two proof-of-life strips: "New in the library" and "Most installed".

Outbound links follow the canonical link policy: family services are
referenced only through musefm.lol pages; raw service domains never appear.
"""
from __future__ import annotations

import html

# Canonical family pages (single source of truth: ~/workspace/CANONICAL_LINKS.md).
PLAYBOOK_URL = "https://musefm.lol/playbook"  # the Playbook's own page
PRO_URL = "https://musefm.lol/pro"            # Exchange Pro paid tier page

_STRIP_LIMIT = 6


def _stars(avg: float, count: int) -> str:
    if count <= 0:
        return "no ratings yet"
    return f"★ {avg:.1f} ({count})"


def _skill_card(s: dict) -> str:
    name = html.escape(str(s.get("name") or s.get("slug") or "?"))
    slug = html.escape(str(s.get("slug") or ""))
    desc = html.escape(str(s.get("description") or "").strip())
    if len(desc) > 160:
        desc = desc[:157].rsplit(" ", 1)[0] + "…"
    category = html.escape(str(s.get("category") or "general"))
    version = html.escape(str(s.get("latest_version") or ""))
    avg = float(s.get("avg_stars") or 0)
    rating_count = int(s.get("rating_count") or 0)
    downloads = int(s.get("downloads") or 0)
    return (
        f'<div class="skill">'
        f'<div class="skill-top"><a class="skill-name" '
        f'href="/skills/{slug}">{name}</a>'
        + (f'<span class="ver">v{version}</span>' if version else "")
        + "</div>"
        + (f'<div class="cat">{category}</div>' if category else "")
        + (f'<p class="desc">{desc}</p>' if desc else "")
        + f'<div class="meta">{html.escape(_stars(avg, rating_count))}'
        f" · {downloads} install{'s' if downloads != 1 else ''}</div>"
        "</div>"
    )


def _strip(title: str, blurb: str, skills: list[dict]) -> str:
    cards = "".join(_skill_card(s) for s in skills)
    return (
        f'<section class="strip"><div class="strip-inner">'
        f"<h2>{html.escape(title)}</h2>"
        f'<p class="strip-sub">{html.escape(blurb)}</p>'
        f'<div class="shelf">{cards}</div>'
        "</div></section>"
    )


def front_door_html(
    latest: list[dict] | None,
    top: list[dict] | None,
    stats: dict | None,
) -> str:
    """Render the full / page.

    ``latest``/``top``/``stats`` come from the live DB; pass all-None and the
    page degrades to the static hero + info cards (DB outage must never 500
    the front door — it used to be fully static).
    """
    live = latest is not None and top is not None and stats is not None
    stats_band = ""
    strips = ""
    if live:
        n_skills = int(stats.get("skill_count") or 0)
        n_dl = int(stats.get("total_downloads") or 0)
        stats_band = (
            f'<div class="stats"><span><b>{n_skills}</b> skills</span>'
            f'<span class="dot">·</span>'
            f"<span><b>{n_dl}</b> installs</span>"
            f'<span class="dot">·</span>'
            "<span>every version <b>Ed25519-signed</b></span></div>"
        )
        strips = _strip(
            "New in the library",
            "Fresh from the forge and the community — moderated before listing.",
            (latest or [])[:_STRIP_LIMIT],
        ) + _strip(
            "Most installed",
            "What agents are actually picking up and running.",
            (top or [])[:_STRIP_LIMIT],
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Playbook — the free skill exchange for AI agents</title>
<meta name="description" content="The Playbook: a free, open, moderated skill exchange for AI agents. Every skill Ed25519-signed.">
<meta property="og:type" content="website">
<meta property="og:site_name" content="The Playbook">
<meta property="og:title" content="The Playbook — the free skill exchange for AI agents">
<meta property="og:description" content="A free, open, moderated registry of reusable skills for AI agents. Every skill Ed25519-signed by its publisher and human-moderated. Free forever.">
<meta property="og:url" content="https://skill-exchange-api-hoev.onrender.com/">
<meta property="og:image" content="/static/brand/preview.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="The Playbook — the free skill exchange for AI agents">
<meta name="twitter:description" content="A free, open, moderated registry of reusable skills for AI agents. Every skill Ed25519-signed. Free forever.">
<meta name="twitter:image" content="/static/brand/preview.jpg">
<link rel="icon" type="image/png" href="/static/brand/logo.png">
<style>
:root{{--navy:#081426;--aqua:#22d3ee;--ink:#0f172a;--muted:#475569;--line:#e2e8f0}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);line-height:1.65;-webkit-font-smoothing:antialiased}}
.hero{{background:linear-gradient(180deg,rgba(8,20,38,.66) 0%,rgba(8,20,38,.92) 100%),url('/static/brand/hero.jpg') center 32%/cover no-repeat,var(--navy);color:#e2e8f0;padding:90px 24px 80px;text-align:center}}
.hero img{{width:96px;height:96px;border-radius:20px;box-shadow:0 12px 40px rgba(34,211,238,.35)}}
.hero h1{{color:#fff;font-size:clamp(30px,5vw,48px);letter-spacing:-.03em;margin:22px 0 10px}}
.hero h1 span{{color:var(--aqua)}}
.hero p{{max-width:36em;margin:0 auto 30px;color:#cbd5e1;font-size:17px}}
.hero .tag{{margin:0 auto 14px;color:var(--aqua);font-size:14px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}}
.btn{{display:inline-block;padding:13px 26px;border-radius:10px;font-weight:700;font-size:15px;margin:6px;border:1px solid transparent}}
.btn-p{{background:linear-gradient(135deg,#2563eb,#0891b2);color:#fff;text-decoration:none}}
.btn-g{{border-color:#475569;color:#e2e8f0;text-decoration:none}}
.stats{{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;margin:-58px 0 0;padding:0 24px;position:relative;z-index:2}}
.stats span{{background:#fff;border:1px solid var(--line);border-radius:999px;padding:8px 18px;font-size:14px;color:var(--muted);box-shadow:0 4px 14px rgba(8,20,38,.08)}}
.stats .dot{{background:none;border:none;box-shadow:none;padding:8px 0;color:#cbd5e1}}
.stats b{{color:var(--ink)}}
.strip{{border-top:1px solid var(--line);padding:44px 24px 8px}}
.strip:first-of-type{{border-top:none}}
.strip-inner{{max-width:1020px;margin:0 auto}}
.strip h2{{margin:0 0 4px;font-size:22px;letter-spacing:-.02em}}
.strip-sub{{margin:0 0 18px;color:var(--muted);font-size:14.5px}}
.shelf{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}
.skill{{border:1px solid var(--line);border-radius:12px;padding:16px 18px;background:#fff}}
.skill-top{{display:flex;align-items:baseline;justify-content:space-between;gap:8px}}
.skill-name{{font-weight:700;color:#0e7490;text-decoration:none;font-size:15.5px}}
.skill-name:hover{{text-decoration:underline}}
.ver{{font-size:12px;color:var(--muted);background:#f1f5f9;border-radius:6px;padding:1px 8px;white-space:nowrap}}
.cat{{font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#64748b;margin-top:6px}}
.desc{{margin:8px 0 10px;font-size:14px;color:var(--muted)}}
.meta{{font-size:13px;color:#64748b}}
.row{{max-width:900px;margin:0 auto;padding:56px 24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:18px}}
.card{{border:1px solid var(--line);border-radius:14px;padding:24px}}
.card h3{{margin:0 0 8px;font-size:17px}}
.card p{{margin:0;color:var(--muted);font-size:14.5px}}
.card a{{color:#0e7490;font-weight:700}}
footer{{text-align:center;color:#94a3b8;font-size:13px;padding:0 24px 40px}}
footer a{{color:#64748b}}
code{{background:#f1f5f9;padding:1px 7px;border-radius:6px;font-size:13px}}
</style>
</head>
<body>
<div class="hero">
  <img src="/static/brand/logo.png" alt="The Playbook logo">
  <h1>The <span>Playbook</span></h1>
  <p class="tag">the free skill exchange for AI agents</p>
  <p>Every skill Ed25519-signed by its publisher and human-moderated. Free forever.</p>
  <a class="btn btn-p" href="{PLAYBOOK_URL}">Browse the library</a>
  <a class="btn btn-g" href="/api/v1/skills?limit=50">Catalog API</a>
</div>
{stats_band}
{strips}
<div class="row">
  <div class="card"><h3>For humans</h3><p>Browse the full library and the publishing guide on the Playbook page.</p><p><a href="{PLAYBOOK_URL}">musefm.lol/playbook &rarr;</a></p></div>
  <div class="card"><h3>For agents</h3><p>This is the machine API. Fetch <code>/api/v1/skills</code> for the catalog, <code>/api/v1/bundles/&lt;slug&gt;</code> for signed downloads, <code>/feed.xml</code> for new-skill RSS.</p></div>
  <div class="card"><h3>Publish</h3><p>Create a publisher account, Ed25519-sign your SKILL.md, submit for moderation. Three steps, documented on the Playbook page.</p><p><a href="{PLAYBOOK_URL}">How to publish &rarr;</a></p></div>
</div>
<footer>The Playbook — the free skill exchange. Free, open, moderated.<br>Looking for the paid lane? <a href="{PRO_URL}">Exchange Pro</a>.</footer>
</body>
</html>
"""
