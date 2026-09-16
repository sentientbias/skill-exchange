"""End-to-end referral flow test (needs local Postgres + migration 001/002).

Covers: account creation with referred_by (valid/invalid/self), skill submit,
moderation approval -> referral converts + pro pass issued, pass retrieval,
operator listing, idempotency (second skill approval doesn't double-issue),
and pass verification.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, propass, signing, store  # noqa: E402

SIGNING_KEY = os.environ["PROPASS_SIGNING_KEY"]
VERIFY_KEY = os.environ["PROPASS_VERIFY_KEY"]


def make_skill(slug):
    md = "# " + slug + "\n\n" + ("A test skill for the referral flow. " * 10)
    priv, pub = signing.generate_keypair()
    sig = signing.sign_package(slug, "1.0.0", md, priv)
    return dict(
        name=slug, slug=slug, description="referral test skill",
        category="general", version="1.0.0", skill_md=md,
        manifest={}, signature=sig, public_key=pub,
    )


async def main():
    pool = await db.get_pool()
    # fresh slate for test handles
    await pool.execute(
        "delete from accounts where handle like 'reftest-%'")
    try:
        # 1. referrer account (no referred_by)
        referrer = await store.create_account(
            pool, "reftest-referrer", "Ref Test Referrer")
        # 2. referred account with referred_by
        referred = await store.create_account(
            pool, "reftest-referred", "Ref Test Referred",
            referred_by="reftest-referrer")
        assert referred["handle"] == "reftest-referred"
        print("1-2. accounts created with referral: OK")

        # 3. bad referrals rejected
        for bad, why in [
            ("no-such-handle", "unknown referrer"),
            ("reftest-badself", "self referral"),
        ]:
            try:
                if why == "self referral":
                    await store.create_account(
                        pool, "reftest-badself", "x", referred_by="reftest-badself")
                else:
                    await store.create_account(
                        pool, "reftest-bad", "x", referred_by=bad)
                raise AssertionError(f"{why} should have failed")
            except ValueError:
                pass
        print("3. invalid/self referrals rejected: OK")

        # 4. referred publisher submits a skill (goes to moderation)
        skill = await store.create_skill(
            pool, referred["id"], **make_skill("reftest-skill"))
        assert skill["status"] == "pending", skill
        q = await store.moderation_queue(pool)
        item = next(i for i in q if i["slug"] == "reftest-skill")
        print("4. skill submitted, queued: OK")

        # 5. moderator approves -> referral auto-converts, pass issued
        res = await store.decide_moderation(
            pool, referrer["id"], str(item["id"]), True, "test approval")
        assert res["decision"] == "approved"
        assert res["referral"]["converted"], res["referral"]
        assert res["referral"]["referrer"] == "reftest-referrer"
        print("5. approval auto-converts referral, pass issued: OK",
              res["referral"]["pass_id"])

        # 6. referrer retrieves their pass; token verifies
        passes = await store.list_pro_passes(pool, referrer["id"])
        assert len(passes) == 1
        payload = propass.verify_pass(passes[0]["token"], VERIFY_KEY)
        assert payload and payload["handle"] == "reftest-referrer"
        print("6. pass retrieval + signature verify: OK")

        # 7. operator listing shows converted referral
        refs = await store.list_referrals(pool)
        row = next(r for r in refs if r["referred_handle"] == "reftest-referred")
        assert row["status"] == "converted" and row["pass_id"] == passes[0]["pass_id"]
        print("7. operator referral listing: OK")

        # 8. second skill approval does NOT issue another pass (idempotent)
        skill2 = await store.create_skill(
            pool, referred["id"], **make_skill("reftest-skill-two"))
        q2 = await store.moderation_queue(pool)
        item2 = next(i for i in q2 if i["slug"] == "reftest-skill-two")
        res2 = await store.decide_moderation(
            pool, referrer["id"], str(item2["id"]), True, "test")
        assert not res2["referral"]["converted"], res2["referral"]
        passes2 = await store.list_pro_passes(pool, referrer["id"])
        assert len(passes2) == 1, "must not double-issue"
        print("8. idempotent (no double issue): OK")

        # 9. approval of a non-referred publisher converts nothing
        loner = await store.create_account(pool, "reftest-loner", "Loner")
        s3 = await store.create_skill(pool, loner["id"], **make_skill("reftest-s3"))
        q3 = await store.moderation_queue(pool)
        i3 = next(i for i in q3 if i["slug"] == "reftest-s3")
        res3 = await store.decide_moderation(pool, referrer["id"], str(i3["id"]), True, "t")
        assert not res3["referral"]["converted"]
        print("9. non-referred approval converts nothing: OK")

        print("\nALL REFERRAL FLOW TESTS PASSED")
    finally:
        await pool.execute(
            "delete from accounts where handle like 'reftest-%'")
        await db.close_pool()


asyncio.run(main())
