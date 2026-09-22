"""Manifest shape: the API must return manifest as a JSON object, never a
double-encoded JSON string.

asyncpg returns jsonb columns as str unless a codec is registered, so rows
arrive with ``manifest`` as e.g. ``'{}'``. core.store._d() normalizes it to
a dict at the store boundary; these tests pin that contract (and that the
bundle zip's manifest.json is a real object).
"""
from __future__ import annotations

import io
import json
import zipfile

from api.routers.bundles import _build_zip
from core import store


def _row(**kw):
    return {"id": "x", "version": "1.0.0", **kw}


def test_d_converts_stringified_manifest_to_dict():
    out = store._d(_row(manifest='{"requires": ["python>=3.11"]}'))
    assert out["manifest"] == {"requires": ["python>=3.11"]}
    assert isinstance(out["manifest"], dict)


def test_d_converts_empty_stringified_manifest_to_empty_dict():
    out = store._d(_row(manifest="{}"))
    assert out["manifest"] == {}


def test_d_passes_through_dict_manifest():
    m = {"a": 1}
    out = store._d(_row(manifest=m))
    assert out["manifest"] == {"a": 1}


def test_d_handles_garbage_manifest_string():
    out = store._d(_row(manifest="not-json{{"))
    assert out["manifest"] == {}


def test_d_handles_none_manifest():
    out = store._d(_row(manifest=None))
    assert out["manifest"] == {}


def test_d_handles_non_dict_json_manifest():
    # A stored manifest that parses to a list is not a valid manifest object.
    out = store._d(_row(manifest="[1, 2]"))
    assert out["manifest"] == {}


def test_d_leaves_rows_without_manifest_untouched():
    out = store._d({"id": "y", "handle": "zuckbot"})
    assert out == {"id": "y", "handle": "zuckbot"}


def test_d_none_row_stays_none():
    assert store._d(None) is None


def test_manifest_to_dict_unit_cases():
    f = store._manifest_to_dict
    assert f({"a": 1}) == {"a": 1}
    assert f('{"a": 1}') == {"a": 1}
    assert f("{}") == {}
    assert f("") == {}
    assert f(None) == {}
    assert f(123) == {}


def test_build_zip_manifest_json_is_object_not_string():
    ver = store._d(
        _row(
            skill_md="# test",
            manifest='{"entry": "SKILL.md"}',
            signature="sig",
            signer_pubkey="pub",
        )
    )
    zf = zipfile.ZipFile(io.BytesIO(_build_zip("demo-skill", ver)))
    manifest = json.loads(zf.read("demo-skill/manifest.json").decode("utf-8"))
    assert manifest == {"entry": "SKILL.md"}
    assert isinstance(manifest, dict)
