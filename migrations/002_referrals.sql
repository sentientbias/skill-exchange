-- Skill Exchange schema, migration 002: referral program + pro passes.
-- Apply with:  psql "$DATABASE_URL" -f migrations/002_referrals.sql
-- Safe to re-run: every statement is IF NOT EXISTS / guarded.

-- Who referred this account (handle as typed at signup; may be null).
alter table accounts
  add column if not exists referred_by_handle text;

-- One row per referred account. A referral converts when the referred
-- publisher's FIRST skill is approved through moderation; the referrer then
-- earns a pro pass (free Exchange Pro access).
create table if not exists referrals (
  id                  uuid primary key default gen_random_uuid(),
  referrer_account_id uuid not null references accounts(id) on delete cascade,
  referred_account_id uuid not null references accounts(id) on delete cascade,
  status              text not null default 'pending'
                      check (status in ('pending', 'converted')),
  created_at          timestamptz not null default now(),
  converted_at        timestamptz,
  unique (referred_account_id)
);
create index if not exists referrals_referrer_idx on referrals (referrer_account_id);
create index if not exists referrals_status_idx on referrals (status);

-- Issued pro passes. The token itself is an ed25519-signed bearer token
-- (see core/propass.py); it is stored here so the referrer can retrieve it
-- later via GET /api/v1/accounts/me/pro-passes. Passes expire 90 days after
-- issue and are bound to the referrer's handle inside the signed payload.
create table if not exists pro_passes (
  id          uuid primary key default gen_random_uuid(),
  account_id  uuid not null references accounts(id) on delete cascade,
  referral_id uuid references referrals(id) on delete set null,
  pass_id     text not null unique,
  token       text not null,
  issued_at   timestamptz not null default now(),
  expires_at  timestamptz not null
);
create index if not exists pro_passes_account_idx on pro_passes (account_id);
