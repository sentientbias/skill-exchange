"""Malformed queue_id on the moderation decide route.

POST /api/v1/moderation/queue/{queue_id}/decide fed the raw path param into
``store.decide_moderation``, which casts it to ``::uuid``. Garbage input
raised inside asyncpg and surfaced as a 500. The route now validates the
UUID at the boundary and answers 404 "no such queue item" (same pattern as
DELETE /accounts/me/keys/{key_id} from test_key_revoke_hardening.py).

Run:  pytest tests/test_moderation_queue_id.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from api.routers.moderation import decide
from api.schemas import ModerateIn
from core import store
from fastapi import HTTPException


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def _stub_decide(monkeypatch):
    calls = []

    async def fake(pool, reviewer_id, queue_id, approve, note):
        calls.append((reviewer_id, queue_id, approve, note))
        return {"ok": True, "queue_id": queue_id, "approved": approve}

    monkeypatch.setattr(store, "decide_moderation", fake)
    return calls


MALFORMED = ["not-a-uuid", "", "../etc/passwd", "12345", "x" * 64,
             "a462ecb5-8dcf-4536-acaf-2af81e765ced-extra"]


@pytest.mark.parametrize("queue_id", MALFORMED)
def test_malformed_queue_id_404_without_touching_store(_stub_decide,
                                                       queue_id):
    calls = _stub_decide
    with pytest.raises(HTTPException) as exc:
        _run(decide(queue_id, ModerateIn(approve=True),
                    account={"id": "acct-1"}, pool=object()))
    assert exc.value.status_code == 404
    assert exc.value.detail == "no such queue item"
    assert calls == [], "store must never be reached on malformed input"


def test_well_formed_uuid_reaches_store(_stub_decide):
    calls = _stub_decide
    qid = "a462ecb5-8dcf-4536-acaf-2af81e765ced"
    out = _run(decide(qid, ModerateIn(approve=False, note="junk"),
                      account={"id": "acct-1"}, pool=object()))
    assert out["ok"] is True
    assert out["queue_id"] == qid
    assert len(calls) == 1
    reviewer_id, got_qid, approve, note = calls[0]
    assert got_qid == qid
    assert approve is False
    assert note == "junk"


def test_uppercase_uuid_still_valid(_stub_decide):
    calls = _stub_decide
    qid = "A462ECB5-8DCF-4536-ACAF-2AF81E765CED"
    out = _run(decide(qid, ModerateIn(approve=True),
                      account={"id": "acct-1"}, pool=object()))
    assert out["ok"] is True
    assert calls[0][1] == qid
