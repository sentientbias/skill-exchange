-- Skill Exchange schema, migration 003: Row-Level Security.
--
-- Supabase flags every table in the public schema without RLS as
-- "publicly accessible" (rls_disabled_in_public), and api_keys /
-- pro_passes additionally trip "sensitive data publicly accessible"
-- (sensitive_columns_exposed), because the built-in PostgREST API would
-- serve them to anyone holding the anon key.
--
-- All real traffic goes through the FastAPI backend, whose DATABASE_URL
-- authenticates as the postgres superuser (bypasses RLS), so this
-- migration only hardens the PostgREST surface and cannot break the API.
--
-- Design:
--   * RLS ENABLED on every table.
--   * Public catalog tables (skills, skill_versions, ratings) get
--     read-only SELECT policies matching what the public API serves.
--   * Backend-only tables (accounts, api_keys, moderation_queue,
--     install_events, referrals, pro_passes) get NO policies:
--     anon/authenticated PostgREST roles are denied everything.
--
-- Safe to re-run: every statement is idempotent.
-- Apply with:  psql "$DATABASE_URL" -f migrations/003_rls.sql
-- or paste into the Supabase SQL editor.

-- 1. Enable RLS on all tables -------------------------------------------
alter table accounts          enable row level security;
alter table api_keys          enable row level security;
alter table skills            enable row level security;
alter table skill_versions    enable row level security;
alter table ratings           enable row level security;
alter table moderation_queue  enable row level security;
alter table install_events    enable row level security;
alter table referrals         enable row level security;
alter table pro_passes        enable row level security;

-- 2. Public catalog: read-only -------------------------------------------
-- Approved skills only; pending/rejected stay hidden.
drop policy if exists "public read approved skills" on skills;
create policy "public read approved skills"
  on skills for select
  using (status = 'approved');

-- Versions belonging to approved skills.
drop policy if exists "public read versions of approved skills" on skill_versions;
create policy "public read versions of approved skills"
  on skill_versions for select
  using (exists (
    select 1 from skills s
    where s.id = skill_versions.skill_id
      and s.status = 'approved'
  ));

-- Ratings are public in the catalog.
drop policy if exists "public read ratings" on ratings;
create policy "public read ratings"
  on ratings for select
  using (true);

-- 3. Backend-only tables: accounts, api_keys, moderation_queue,
--    install_events, referrals, pro_passes — intentionally NO policies.
--    PostgREST anon/authenticated roles are denied everything; the
--    backend connects as postgres (superuser) and bypasses RLS.
