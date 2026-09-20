"""Per-skill detail page (GET /skills/{slug}): the registry package-page standard.

npm, PyPI, and the Hugging Face Hub all give every package a canonical
human-readable page: install command, version history, provenance, ratings.
The Playbook's front-door cards linked straight to a raw SKILL.md download,
which is a dead end for a human visitor deciding whether to install. This
page closes that gap: server-rendered, no JS (same constraint as the front
door, so plain-HTTP agent clients read it too).

Only approved skills get pages (store.get_skill filters status unless asked
otherwise). Slugs are strictly validated by core.store._check_slug before any
DB touch; unknown slugs, invalid slugs, and DB outages all render the 404
page, never a 500. Everything dynamic is html-escaped (same discipline as
api/front_door.py). The signer public key shown here is already public via
the signed-bundle receipt endpoint -- no secret material appears.

Outbound links follow the canonical link policy: family services are
referenced only through musefm.lol pages; raw service domains never appear.
"""
from __future__ import annotations

import html

# Canonical family pages (single source of truth: ~/workspace/CANONICAL_LINKS.md).
PLAYBOOK_URL = "https://musefm.lol/playbook"  # the Playbook's own page
PRO_URL = "https://musefm.lol/pro"            # Exchange Pro paid tier page


def _stars(avg: float, count: int) -> str:
    if count <= 0:
        return "no ratings yet"
    return f"★ {avg:.1f} ({count})"


def _date(value) -> str:
    """Render a DB date/datetime as YYYY-MM-DD; tolerate None/strings."""
    if value is None:
        return "—"
    s = str(value)
    return s[:10] if len(s) >= 10 else s


def _version_row(v: dict) -> str:
    version = html.escape(str(v.get("version") or ""))
    date = html.escape(_date(v.get("created_at")))
    downloads = int(v.get("downloads") or 0)
    return (
        f'<div class="ver-row"><span class="ver">v{version}</span>'
        f'<span class="ver-meta">{date} · '
        f"{downloads} install{'s' if downloads != 1 else ''} · "
        "Ed25519-signed</span></div>"
    )


def _rating_row(r: dict) -> str:
    stars = int(r.get("stars") or 0)
    handle = html.escape(str(r.get("handle") or "anonymous"))
    date = html.escape(_date(r.get("created_at")))
    comment = str(r.get("comment") or "").strip()
    comment_html = (
        f"<p class=\"rating-comment\">{html.escape(comment)}</p>" if comment else ""
    )
    return (
        f'<div class="rating"><div class="rating-head">'
        f"<span>{'★' * stars}{'☆' * (5 - stars)}</span>"
        f'<span class="rating-who">{handle} · {date}</span>'
        f"</div>{comment_html}</div>"
    )


def skill_page_html(skill: dict) -> str:
    """Render the full detail page for one approved skill dict (see
    core.store.get_skill: metadata + ``versions`` + ``recent_ratings``)."""
    name = html.escape(str(skill.get("name") or skill.get("slug") or "?"))
    slug = html.escape(str(skill.get("slug") or ""))
    desc = html.escape(str(skill.get("description") or "").strip())
    category = html.escape(str(skill.get("category") or "general"))
    publisher = html.escape(str(skill.get("publisher") or "unknown"))
    version = html.escape(str(skill.get("latest_version") or ""))
    avg = float(skill.get("avg_stars") or 0)
    rating_count = int(skill.get("rating_count") or 0)
    downloads = int(skill.get("downloads") or 0)
    published = html.escape(_date(skill.get("created_at")))

    versions = skill.get("versions") or []
    version_rows = "".join(_version_row(v) for v in versions)

    latest_pubkey = ""
    if versions:
        latest_pubkey = str(versions[-1].get("signer_pubkey") or "")
    pubkey_html = (
        f'<p class="pubkey">publisher key '
        f"<code>{html.escape(latest_pubkey)}</code></p>"
        if latest_pubkey
        else ""
    )

    ratings = skill.get("recent_ratings") or []
    ratings_html = (
        "".join(_rating_row(r) for r in ratings)
        if ratings
        else '<p class="muted">No ratings yet — be the first to rate it.</p>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — The Playbook</title>
<meta name="description" content="{html.escape(str(skill.get('description') or '')[:160])}">
<style>
:root{{--navy:#081426;--aqua:#22d3ee;--ink:#0f172a;--muted:#475569;--line:#e2e8f0}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);line-height:1.65;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:760px;margin:0 auto;padding:0 24px}}
.crumbs{{padding:22px 0 0;font-size:13px;color:#64748b}}
.crumbs a{{color:#0e7490;text-decoration:none}}
.crumbs a:hover{{text-decoration:underline}}
h1{{font-size:clamp(28px,4.5vw,40px);letter-spacing:-.03em;margin:10px 0 4px}}
.ver{{font-size:12px;color:var(--muted);background:#f1f5f9;border-radius:6px;padding:1px 8px;white-space:nowrap}}
.cat{{font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#64748b}}
.meta{{font-size:14px;color:var(--muted);margin:6px 0 0}}
.lede{{font-size:17px;color:var(--ink);margin:18px 0 30px}}
.stats{{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 34px}}
.stats span{{background:#f8fafc;border:1px solid var(--line);border-radius:999px;padding:6px 16px;font-size:14px;color:var(--muted)}}
.stats b{{color:var(--ink)}}
h2{{font-size:19px;letter-spacing:-.02em;margin:34px 0 10px}}
.install{{background:#0f172a;color:#e2e8f0;border-radius:12px;padding:18px 20px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14.5px;overflow-x:auto}}
.install .c{{color:#64748b}}
.links{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 0}}
.btn{{display:inline-block;padding:10px 20px;border-radius:10px;font-weight:700;font-size:14px;text-decoration:none;border:1px solid var(--line);color:#0e7490;background:#fff}}
.btn:hover{{border-color:#0e7490}}
.ver-row{{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}}
.ver-row:last-child{{border-bottom:none}}
.ver-meta{{font-size:13.5px;color:var(--muted)}}
.pubkey{{font-size:13px;color:var(--muted);margin:12px 0 0;word-break:break-all}}
.rating{{border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:0 0 10px}}
.rating-head{{display:flex;justify-content:space-between;gap:10px;font-size:14px}}
.rating-head span:first-child{{color:#b45309;letter-spacing:.1em}}
.rating-who{{color:#64748b;font-size:13px}}
.rating-comment{{margin:8px 0 0;font-size:14.5px;color:var(--muted)}}
.muted{{color:var(--muted);font-size:14.5px}}
footer{{text-align:center;color:#94a3b8;font-size:13px;padding:48px 24px 40px}}
footer a{{color:#64748b}}
code{{background:#f1f5f9;padding:1px 7px;border-radius:6px;font-size:13px}}
.install code{{background:none;padding:0;color:inherit;font-size:inherit}}
</style>
</head>
<body>
<div class="wrap">
  <div class="crumbs"><a href="/">The Playbook</a> / skills / {slug}</div>
  <h1>{name} <span class="ver">v{version}</span></h1>
  <div class="cat">{category}</div>
  <p class="meta">by {publisher} · published {published}</p>
  <p class="lede">{desc}</p>
  <div class="stats">
    <span><b>{downloads}</b> install{'s' if downloads != 1 else ''}</span>
    <span>{html.escape(_stars(avg, rating_count))}</span>
    <span>every version <b>Ed25519-signed</b></span>
  </div>
  <h2>Install</h2>
  <div class="install"><span class="c"># fetch the signed package, verify the Ed25519 signature, install</span><br><code>./install.sh {slug}</code></div>
  <div class="links">
    <a class="btn" href="/api/v1/bundles/{slug}">Signed bundle (.zip)</a>
    <a class="btn" href="/api/v1/skills/{slug}/skill.md">Read the SKILL.md</a>
    <a class="btn" href="/api/v1/skills/{slug}">Machine JSON</a>
  </div>
  <h2>Versions</h2>
  {version_rows}
  {pubkey_html}
  <h2>Ratings</h2>
  {ratings_html}
</div>
<footer>The Playbook — the free skill exchange. Free, open, moderated.<br><a href="{PLAYBOOK_URL}">Browse the library</a> · <a href="{PRO_URL}">Exchange Pro</a></footer>
</body>
</html>
"""


def skill_not_found_html(slug: str) -> str:
    """404 page for unknown/invalid slugs or DB outages. Never a 500."""
    slug = html.escape(str(slug or ""))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Skill not found — The Playbook</title>
<style>
body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#0f172a;line-height:1.65;text-align:center;padding:90px 24px}}
h1{{font-size:32px;letter-spacing:-.02em}}
p{{color:#475569}}
a{{color:#0e7490;font-weight:700;text-decoration:none}}
</style>
</head>
<body>
<h1>No such skill</h1>
<p>There's no approved skill called <b>{slug}</b> in the Playbook.</p>
<p><a href="/">&larr; Back to the library</a></p>
</body>
</html>
"""
