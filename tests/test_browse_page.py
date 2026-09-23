"""Browse page (GET /browse): searchable, filterable catalog surface.

Design under test: the registry browse slot every major registry ships
(npm, PyPI, Hugging Face Hub) — server-rendered, no JS, plain GET forms
and links, so plain-HTTP agent clients read it too.

Run:  pytest tests/test_browse_page.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.browse_page import browse_error_html, browse_page_html, _PER_PAGE
from api.front_door import front_door_html

_SAMPLE = [
    {
        "name": "Regex Mastery",
        "slug": "regex-mastery",
        "description": "Practical regular expressions for agents.",
        "category": "devtools",
        "latest_version": "1.2.0",
        "avg_stars": 4.5,
        "rating_count": 4,
        "downloads": 8,
    },
    {
        "name": "Cold Email Drafting",
        "slug": "cold-email-drafting",
        "description": "Draft cold emails that get replies.",
        "category": "writing",
        "latest_version": "1.0.0",
        "avg_stars": 0.0,
        "rating_count": 0,
        "downloads": 7,
    },
]
_CATS = [
    {"category": "devtools", "count": 12},
    {"category": "writing", "count": 9},
]


def _page(**kw):
    base = dict(
        q="",
        category="",
        sort="newest",
        page=1,
        total=len(_SAMPLE),
        results=_SAMPLE,
        categories=_CATS,
    )
    base.update(kw)
    return browse_page_html(**base)


# ---------------------------------------------------------------------------
# search form + escaping
# ---------------------------------------------------------------------------

def test_search_form_reflects_query():
    page = _page(q="regex")
    assert 'method="get"' in page
    assert 'action="/browse"' in page
    assert 'value="regex"' in page
    assert 'name="sort"' in page


def test_query_is_html_escaped():
    page = _page(q='"><script>alert(1)</script>')
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_active_category_preserved_as_hidden_field():
    page = _page(category="devtools")
    assert 'name="category" value="devtools"' in page


def test_category_in_search_input_value_is_escaped():
    page = _page(q="a&b=c?d")
    assert 'value="a&amp;b=c?d"' in page


# ---------------------------------------------------------------------------
# category chips
# ---------------------------------------------------------------------------

def test_chips_render_with_counts_and_all_chip():
    page = _page()
    assert "All" in page
    assert "devtools" in page
    assert "writing" in page
    # chip labels carry their counts
    assert ">devtools" in page


def test_active_chip_marked_and_links_preserve_state():
    page = _page(q="email", sort="downloads", category="writing")
    assert 'class="chip on"' in page
    # chip links carry q + sort but reset to page 1 (no page param)
    assert "q=email" in page
    assert "sort=downloads" in page
    assert "page=" not in page.replace('class="pager"', "")


def test_chip_label_is_escaped():
    cats = [{"category": '<img src=x onerror=alert(1)>', "count": 1}]
    page = _page(categories=cats)
    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


# ---------------------------------------------------------------------------
# results grid + pagination
# ---------------------------------------------------------------------------

def test_results_render_cards_with_skill_links():
    page = _page()
    assert "Regex Mastery" in page
    assert "/skills/regex-mastery" in page
    assert "Cold Email Drafting" in page


def test_result_count_line():
    page = _page(total=42)
    assert "<b>42</b> skills" in page


def test_singular_count():
    page = _page(total=1, results=_SAMPLE[:1])
    assert "<b>1</b> skill</p>" in page


def test_middle_page_has_prev_and_next_with_state():
    page = _page(q="email", category="writing", sort="top", page=2, total=45)
    assert "← Newer" in page
    assert "Older →" in page
    assert "page 2 of 3" in page
    assert "q=email" in page
    assert "category=writing" in page
    assert "sort=top" in page


def test_first_page_has_no_prev():
    page = _page(page=1, total=45)
    assert "← Newer" not in page
    assert "Older →" in page


def test_last_page_has_no_next():
    page = _page(page=3, total=45)
    assert "Older →" not in page
    assert "← Newer" in page


def test_no_pager_when_single_page():
    page = _page(page=1, total=2)
    assert "page 1 of" not in page


# ---------------------------------------------------------------------------
# empty state + sort select
# ---------------------------------------------------------------------------

def test_empty_state_with_clear_link():
    page = _page(total=0, results=[], q="zzz-no-match")
    assert "No skills match" in page
    assert 'href="/browse"' in page


def test_sort_select_marks_active():
    page = _page(sort="downloads")
    assert '<option value="downloads" selected>' in page
    assert '<option value="newest">' in page  # no selected attr


# ---------------------------------------------------------------------------
# wiring: front door CTA points at the browse page; error page never 500s
# ---------------------------------------------------------------------------

def test_front_door_cta_links_to_browse():
    page = front_door_html(_SAMPLE, _SAMPLE, {"skill_count": 50, "total_downloads": 31})
    assert 'href="/browse"' in page
    assert "Browse the library" in page


def test_error_page_is_safe_html():
    page = browse_error_html()
    assert "temporarily unavailable" in page
    assert "/api/v1/skills" in page
