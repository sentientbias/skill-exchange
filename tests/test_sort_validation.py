"""Tests for core.store._check_sort: invalid sort values must be rejected,
never silently fall back to newest-first (Museoh bounty report, 2026-09-20).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.store import _SORTS, _check_sort


def test_all_documented_sorts_pass():
    for sort in ("newest", "top", "downloads", "name"):
        assert sort in _SORTS
        _check_sort(sort)  # no raise


def test_invalid_sort_raises_value_error():
    for bad in ("bogus", "oldest", "NAME", "popular", "top_rated", "", "newest;drop"):
        with pytest.raises(ValueError, match="sort must be one of"):
            _check_sort(bad)
