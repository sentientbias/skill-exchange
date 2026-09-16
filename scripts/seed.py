#!/usr/bin/env python3
"""Seed the database with the starter skill catalog.

Reads seeds/skills/*/skill.yaml + SKILL.md, creates a curator account, signs
each package with a throwaway keypair, and inserts them as APPROVED (seed data
bypasses moderation by design -- it ships with the project).

Usage:
    DATABASE_URL=postgresql://... python scripts/seed.py

Environment:
    SEED_MODERATOR=false   set to anything else to make the curator a
                           non-moderator (default: curator IS a moderator,
                           handy for local dev).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, signing, store  # noqa: E402

SEEDS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "seeds", "skills",
)


async def main() -> None:
    pool = await db.get_pool()
    try:
        # Curator account (throwaway dev credentials -- local use only).
        try:
            curator = await store.create_account(
                pool,
                handle="curator",
                display_name="Exchange Curator",
                is_moderator=os.environ.get("SEED_MODERATOR", "true").lower()
                not in ("0", "false", "no"),
            )
            print(f"curator account created; API key (show once, dev only):")
            print(f"  {curator['api_key']}")
        except ValueError:
            row = await pool.fetchrow(
                "select * from accounts where handle = 'curator'")
            curator = dict(row)
            print("curator account already exists; skipping creation")

        for slug in sorted(os.listdir(SEEDS_DIR)):
            skill_dir = os.path.join(SEEDS_DIR, slug)
            manifest_path = os.path.join(skill_dir, "skill.yaml")
            md_path = os.path.join(skill_dir, "SKILL.md")
            if not (os.path.isfile(manifest_path) and os.path.isfile(md_path)):
                continue
            with open(manifest_path) as fh:
                meta = yaml.safe_load(fh)
            with open(md_path) as fh:
                skill_md = fh.read()

            # Throwaway keypair: seed signatures are valid, but the private
            # key is discarded -- nobody can publish updates as the curator
            # without the real key (there isn't one).
            private_hex, public_hex = signing.generate_keypair()
            signature = signing.sign_package(
                meta["slug"], meta["version"], skill_md, private_hex)

            exists = await pool.fetchval(
                "select 1 from skills where slug = $1", meta["slug"])
            if exists:
                print(f"  - {meta['slug']}: already seeded, skipping")
                continue

            # Insert directly as approved: seed data bypasses moderation.
            async with pool.acquire() as conn:
                async with conn.transaction():
                    skill = await conn.fetchrow(
                        """insert into skills
                           (slug, name, description, category,
                            author_account_id, status)
                           values ($1, $2, $3, $4, $5::uuid, 'approved')
                           returning *""",
                        meta["slug"], meta["name"], meta["description"],
                        meta["category"], curator["id"],
                    )
                    ver = await conn.fetchrow(
                        """insert into skill_versions
                           (skill_id, version, skill_md, manifest,
                            signature, signer_pubkey)
                           values ($1, $2, $3, $4::jsonb, $5, $6)
                           returning *""",
                        skill["id"], meta["version"], skill_md,
                        json.dumps(meta), signature, public_hex,
                    )
                    await conn.execute(
                        "update skills set latest_version_id = $1 where id = $2",
                        ver["id"], skill["id"],
                    )
            print(f"  + {meta['slug']} v{meta['version']}: seeded (approved)")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
