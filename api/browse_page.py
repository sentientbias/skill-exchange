"""Browse/search page (GET /browse): the registry catalog surface.

npm, PyPI, and the Hugging Face Hub all give their catalog a searchable,
filterable HTML surface -- a search box, category/tag filters, sort orders
-- because a registry's core job is helping a visitor FIND a package among
hundreds. The Playbook's HTML surface was two six-item strips on the front
door ("New in the library", "Most installed") plus a "Browse the library"
button that exited to a different site: a 100+ skill catalog was
effectively unsearchable on the registry itself. This page closes that
gap with the same no-JS, server-rendered discipline as the front door and
skill pages -- plain GET forms and links, so plain-HTTP agent clients read
it too.

Escape-first: every reflected query param is html-escaped at the boundary,
and every link is built with urllib.parse.urlencode so arbitrary input can
never break out of a query string. Outbound links follow the canonical
link policy: family services are referenced only through musefm.lol pages;
raw service domains never appear.
"""
from __future__ import annotations

import html
from urllib.parse import urlencode

from .front_door import PLAYBOOK_URL, _skill_card

SORTS = (
    ("newest", "Newest"),
    ("downloads", "Most installed"),
    ("top", "Top rated"),
    ("name", "A–Z"),
)

_PER_PAGE = 20


def _chip(params: dict, category: str, count: int | None, active: bool) -> str:
    """One category filter chip, as a plain link carrying q/sort/page state.

    The "All" chip carries no category key (empty = no filter), matching
    store.list_skills(category="") semantics.
    """
    p = dict(params)
    if category:
        p["category"] = category
    else:
        p.pop("category", None)
    p.pop("page", None)  # a new filter always starts on page 1
    href = "/browse?" + urlencode(p) if p else "/browse"
    label = "All" if not category else category
    count_html = "" if count is None else f' <span class="chip-n">{count}</span>'
    cls = "chip on" if active else "chip"
    return (
        f'<a class="{cls}" href="{href}">'
        f"{html.escape(label)}{count_html}</a>"
    )


def _page_link(params: dict, page: int, label: str) -> str:
    p = dict(params)
    if page <= 1:
        p.pop("page", None)
    else:
        p["page"] = str(page)
    href = "/browse?" + urlencode(p) if p else "/browse"
    return f'<a class="page-link" href="{href}">{html.escape(label)}</a>'


def browse_page_html(
    *,
    q: str,
    category: str,
    sort: str,
    page: int,
    total: int,
    results: list[dict],
    categories: list[dict],
) -> str:
    """Render the /browse catalog page.

    ``categories`` is the per-category breakdown from
    ``core.store.public_stats`` (``[{"category": ..., "count": ...}]``).
    All params are pre-validated by the route; this is pure rendering.
    """
    q_esc = html.escape(q)
    params = {"q": q, "sort": sort}
    if category:
        params["category"] = category

    sort_opts = "".join(
        f'<option value="{key}"{" selected" if key == sort else ""}>'
        f"{html.escape(label)}</option>"
        for key, label in SORTS
    )
    chips = _chip(params, "", None, not category) + "".join(
        _chip(
            params,
            str(c.get("category") or ""),
            int(c.get("count") or 0),
            str(c.get("category") or "") == category,
        )
        for c in categories
    )

    if total:
        cards = "".join(_skill_card(s) for s in results)
        pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
        prev_html = _page_link(params, page - 1, "← Newer") if page > 1 else ""
        next_html = (
            _page_link(params, page + 1, "Older →") if page < pages else ""
        )
        pager = (
            f'<div class="pager">{prev_html}'
            f'<span class="page-of">page {page} of {pages}</span>'
            f"{next_html}</div>"
            if pages > 1
            else ""
        )
        results_html = (
            f'<p class="count"><b>{total}</b> skill'
            f"{'s' if total != 1 else ''}</p>"
            f'<div class="shelf">{cards}</div>{pager}'
        )
    else:
        results_html = (
            '<div class="empty"><h2>No skills match</h2>'
            "<p>Try a different search term, clear the category filter, "
            'or <a href="/browse">browse the whole library</a>.</p></div>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Browse skills — The Playbook</title>
<meta name="description" content="Browse and search the Playbook: every skill in the free, open, moderated registry for AI agents.">
<meta property="og:type" content="website">
<meta property="og:site_name" content="The Playbook">
<meta property="og:title" content="Browse skills — The Playbook">
<meta property="og:description" content="Search the full catalog of free, Ed25519-signed skills for AI agents.">
<style>
:root{{--navy:#081426;--aqua:#22d3ee;--ink:#0f172a;--muted:#475569;--line:#e2e8f0}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);line-height:1.65;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1020px;margin:0 auto;padding:0 24px}}
.crumbs{{padding:22px 0 0;font-size:13px;color:#64748b}}
.crumbs a{{color:#0e7490;text-decoration:none}}
.crumbs a:hover{{text-decoration:underline}}
h1{{font-size:clamp(26px,4vw,36px);letter-spacing:-.03em;margin:10px 0 18px}}
.searchbar{{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 14px}}
.searchbar input[type="search"]{{flex:1;min-width:220px;font-size:15px;padding:11px 14px;border:1px solid var(--line);border-radius:10px;color:var(--ink)}}
.searchbar input[type="search"]:focus{{outline:2px solid #0e7490;border-color:#0e7490}}
.searchbar select{{font-size:15px;padding:11px 12px;border:1px solid var(--line);border-radius:10px;color:var(--ink);background:#fff}}
.btn{{display:inline-block;padding:11px 22px;border-radius:10px;font-weight:700;font-size:15px;background:linear-gradient(135deg,#2563eb,#0891b2);color:#fff;border:none;cursor:pointer;text-decoration:none}}
.chips{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 24px}}
.chip{{display:inline-block;padding:6px 14px;border:1px solid var(--line);border-radius:999px;font-size:13.5px;color:var(--muted);text-decoration:none;background:#fff}}
.chip:hover{{border-color:#0e7490;color:#0e7490}}
.chip.on{{background:#0f172a;border-color:#0f172a;color:#fff}}
.chip-n{{opacity:.7;font-size:12px}}
.count{{color:var(--muted);font-size:14.5px;margin:0 0 14px}}
.shelf{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}
.skill{{border:1px solid var(--line);border-radius:12px;padding:16px 18px;background:#fff}}
.skill-top{{display:flex;align-items:baseline;justify-content:space-between;gap:8px}}
.skill-name{{font-weight:700;color:#0e7490;text-decoration:none;font-size:15.5px}}
.skill-name:hover{{text-decoration:underline}}
.ver{{font-size:12px;color:var(--muted);background:#f1f5f9;border-radius:6px;padding:1px 8px;white-space:nowrap}}
.cat{{font-size:11.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#64748b;margin-top:6px}}
.desc{{margin:8px 0 10px;font-size:14px;color:var(--muted)}}
.meta{{font-size:13px;color:#64748b}}
.empty{{border:1px dashed var(--line);border-radius:14px;padding:44px 24px;text-align:center;margin:12px 0}}
.empty h2{{margin:0 0 8px;font-size:20px}}
.empty p{{margin:0;color:var(--muted)}}
.empty a{{color:#0e7490;font-weight:700}}
.pager{{display:flex;align-items:center;justify-content:center;gap:18px;margin:30px 0 10px}}
.page-of{{font-size:13.5px;color:var(--muted)}}
.page-link{{font-size:14px;font-weight:700;color:#0e7490;text-decoration:none;border:1px solid var(--line);border-radius:10px;padding:8px 18px;background:#fff}}
.page-link:hover{{border-color:#0e7490}}
footer{{text-align:center;color:#94a3b8;font-size:13px;padding:48px 24px 40px}}
footer a{{color:#64748b}}
</style>
</head>
<body>
<div class="wrap">
  <div class="crumbs"><a href="/">The Playbook</a> / browse</div>
  <h1>Browse the library</h1>
  <form class="searchbar" method="get" action="/browse">
    <input type="search" name="q" value="{q_esc}" placeholder="Search skills by name or topic…">
    <select name="sort" aria-label="Sort order">{sort_opts}</select>
    {'<input type="hidden" name="category" value="' + html.escape(category) + '">' if category else ""}
    <button class="btn" type="submit">Search</button>
  </form>
  <div class="chips">{chips}</div>
  {results_html}
</div>
<footer>The Playbook — the free skill exchange. Free, open, moderated.<br><a href="{PLAYBOOK_URL}">Publishing guide</a></footer>
</body>
</html>
"""


def browse_error_html() -> str:
    """503 page when the DB is unreachable. Never a 500."""
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Catalog unavailable — The Playbook</title>
<style>
body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#0f172a;line-height:1.65;text-align:center;padding:90px 24px}
h1{font-size:32px;letter-spacing:-.02em}
p{color:#475569}
a{color:#0e7490;font-weight:700;text-decoration:none}
</style>
</head>
<body>
<h1>The catalog is temporarily unavailable</h1>
<p>Search and browsing need the database, which we can't reach right now.
The <a href="/api/v1/skills">machine API</a> and the
<a href="/">front door</a> are worth a try.</p>
</body>
</html>
"""
