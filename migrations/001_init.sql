-- Skill Exchange schema, migration 001.
-- Supabase-compatible: uses only gen_random_uuid() (pgcrypto, built in).
-- Apply with:  psql "$DATABASE_URL" -f migrations/001_init.sql

create extension if not exists "pgcrypto";

-- Publisher accounts -------------------------------------------------------
create table accounts (
  id              uuid primary key default gen_random_uuid(),
  handle          text not null unique
                  check (handle ~ '^[a-z0-9][a-z0-9_-]{1,30}$'),
  display_name    text not null check (char_length(display_name) between 1 and 80),
  is_moderator    boolean not null default false,
  created_at      timestamptz not null default now()
);

-- API keys: only SHA-256 hashes are stored. Plaintext is shown once at
-- creation and never persisted.
create table api_keys (
  id              uuid primary key default gen_random_uuid(),
  account_id      uuid not null references accounts(id) on delete cascade,
  key_hash        text not null unique,
  key_prefix      text not null,
  name            text not null default 'default',
  created_at      timestamptz not null default now(),
  last_used_at    timestamptz,
  revoked         boolean not null default false
);
create index api_keys_hash_idx on api_keys (key_hash);
create index api_keys_account_idx on api_keys (account_id);

-- Skills -------------------------------------------------------------------
create table skills (
  id                uuid primary key default gen_random_uuid(),
  slug              text not null unique
                    check (slug ~ '^[a-z0-9][a-z0-9_-]{1,40}$'),
  name              text not null check (char_length(name) between 1 and 120),
  description       text not null check (char_length(description) between 1 and 2000),
  category          text not null default 'general',
  author_account_id uuid not null references accounts(id),
  status            text not null default 'pending'
                    check (status in ('pending', 'approved', 'rejected')),
  latest_version_id uuid,  -- fk added below (circular with skill_versions)
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index skills_status_idx on skills (status);
create index skills_category_idx on skills (category);

-- Immutable published versions. The signature covers
-- slug + "\n" + version + "\n" + skill_md (see core/signing.py).
create table skill_versions (
  id            uuid primary key default gen_random_uuid(),
  skill_id      uuid not null references skills(id) on delete cascade,
  version       text not null check (version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  skill_md      text not null
                check (char_length(skill_md) between 50 and 200000),
  manifest      jsonb not null default '{}',
  signature     text not null,
  signer_pubkey text not null,
  downloads     integer not null default 0,
  created_at    timestamptz not null default now(),
  unique (skill_id, version)
);
create index skill_versions_skill_idx on skill_versions (skill_id);

alter table skills
  add constraint skills_latest_version_fk
  foreign key (latest_version_id) references skill_versions(id);

-- Ratings: one per account per skill, upserted on re-rate -------------------
create table ratings (
  id          uuid primary key default gen_random_uuid(),
  skill_id    uuid not null references skills(id) on delete cascade,
  account_id  uuid not null references accounts(id) on delete cascade,
  stars       smallint not null check (stars between 1 and 5),
  comment     text not null default '' check (char_length(comment) <= 2000),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (skill_id, account_id)
);
create index ratings_skill_idx on ratings (skill_id);

-- Human review queue --------------------------------------------------------
create table moderation_queue (
  id                  uuid primary key default gen_random_uuid(),
  kind                text not null check (kind in ('new_skill', 'new_version')),
  skill_id            uuid not null references skills(id) on delete cascade,
  skill_version_id    uuid references skill_versions(id) on delete cascade,
  submitted_by        uuid not null references accounts(id),
  status              text not null default 'pending'
                      check (status in ('pending', 'approved', 'rejected')),
  reviewer_account_id uuid references accounts(id),
  note                text not null default '',
  created_at          timestamptz not null default now(),
  decided_at          timestamptz
);
create index moderation_queue_status_idx on moderation_queue (status);

-- Install telemetry (feeds download counts; anonymous installs allowed) ------
create table install_events (
  id               uuid primary key default gen_random_uuid(),
  skill_version_id uuid not null references skill_versions(id) on delete cascade,
  account_id       uuid references accounts(id) on delete set null,
  client           text not null default 'unknown',
  installed_at     timestamptz not null default now()
);
create index install_events_version_idx on install_events (skill_version_id);
