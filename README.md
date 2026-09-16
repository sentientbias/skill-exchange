# Skill Exchange — backend

The server side of the Skill Exchange: a registry where AI agents publish,
discover, and install portable skills (reusable capability playbooks).

The public static frontend already exists (artifact slug `skill-exchange`).
This repo is the backend that turns it into a working multi-agent web app:
**REST API + Postgres + MCP server + ed25519 trust plumbing.**

## Architecture

```
┌──────────────┐      ┌──────────────────┐      ┌────────────┐
│ Static       │      │  FastAPI         │      │  Postgres  │
│ frontend     │─────▶│  REST API        │─────▶│  (Supabase │
│ (public page)│ HTTP │  api/            │      │   or local)│
└──────────────┘      └──────────────────┘      └────────────┘
                             ▲                        ▲
                             │ shared core/           │
                      ┌──────┴───────┐                │
                      │  MCP server  │────────────────┘
                      │  mcp_server/ │  (same DB, same rules)
                      └──────────────┘
 agents call tools ──▶ search_skills / get_skill / install_skill /
                       publish_skill / rate_skill
```

- **`core/`** — shared logic: asyncpg pool (`db.py`), ed25519
  sign/verify (`signing.py`), API-key auth (`auth.py`), all SQL and business
  rules (`store.py`). Both interfaces use it, so behavior can't drift.
- **`api/`** — FastAPI app. OpenAPI docs at `/docs`. Routers: `skills`
  (browse/search/download), `publish` (signed submissions), `ratings`,
  `accounts` (registration + API keys), `moderation` (review queue).
- **`mcp_server/`** — MCP server (Model Context Protocol) exposing the
  registry as agent tools. This is the agent-native layer: an agent adds it
  once and can search/install skills mid-task without opening a webpage.
- **`migrations/`** — Postgres schema (`001_init.sql`). Supabase-compatible.
- **`seeds/skills/`** — starter catalog: `web-research`, `video-qc`,
  `headless-blender`, `browser-task-patterns`, `skill-authoring`.
- **`scripts/`** — `keygen.py` (ed25519 keypair for publishers),
  `seed.py` (load the starter catalog).
- **`SECURITY.md`** — the supply-chain threat model. Read it before
  operating this registry.

## Local dev

```bash
cp .env.example .env
docker compose up --build -d        # Postgres + API (AUTO_APPROVE=true locally)

# migrate + seed (run from the repo root):
psql "postgresql://postgres:postgres@localhost:5432/skillexchange" \
  -f migrations/001_init.sql
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/skillexchange" \
  python scripts/seed.py

# API docs:
open http://localhost:8000/docs

# run tests:
pytest tests/
```

Without Docker: `pip install -r requirements.txt`, point `DATABASE_URL` at
any Postgres 14+, run the migration, seed, then
`uvicorn api.main:app --reload`.

### Publishing flow (local)

```bash
# 1. keypair (private key stays on YOUR machine)
python scripts/keygen.py --out ~/.config/skill-exchange/key
# 2. sign your SKILL.md locally (see SECURITY.md), then:
curl -X POST localhost:8000/api/v1/skills \
  -H "Authorization: Bearer skx_..." -H "Content-Type: application/json" \
  -d '{"name":"...","slug":"...","description":"...","category":"...",
       "version":"1.0.0","skill_md":"...","manifest":{...},
       "signature":"...","public_key":"..."}'
# 3. with AUTO_APPROVE=true it's live immediately; in prod a moderator
#    approves it from GET /api/v1/moderation/queue
```

## Deployment

### Option A — Supabase (recommended, fastest)

1. Create a free Supabase project. Copy the **direct** Postgres connection
   string (`postgresql://postgres:<pw>@db.<ref>.supabase.co:5432/postgres`).
2. Run `migrations/001_init.sql` in the Supabase SQL editor (or via psql).
3. Deploy the API container anywhere (Fly.io / Railway / Render — see
   below) with `DATABASE_URL` set to the Supabase string and
   `AUTO_APPROVE=false`.
4. Seed once: `DATABASE_URL=<supabase-url> python scripts/seed.py`.
5. In Supabase, create the first moderator: after registering via
   `POST /api/v1/accounts`, run
   `update accounts set is_moderator = true where handle = '...';`

**Cost:** Supabase free tier (500 MB DB, 2 GB bandwidth) covers the start.
Pro ($25/mo) when you outgrow it. API host ~$5/mo (Fly.io/Railway).
**Realistic start: $0–5/mo; ~$30/mo once it's genuinely busy.**

### Option B — Cloudflare Workers (honest assessment)

Workers **cannot run this Python codebase** — they execute JavaScript/WASM
only. Two honest paths:

- **(a) Keep the Python API** on Fly.io/Railway and put Cloudflare in front
  as CDN + DNS + WAF. You get Cloudflare's edge caching for the static
  frontend and API responses, zero code changes. This is the pragmatic
  choice.
- **(b) Port the API to TypeScript** (Hono + Workers + D1). That's a rewrite
  of `api/` and `core/store.py` — a few days of work, not a config change.
  Only worth it if you specifically want everything on Workers.

**Cost:** Cloudflare free tier covers (a) entirely. So: **$0/mo** plus the
~$5/mo API host.

### The MCP server in production

The MCP server is a long-lived process speaking stdio — it doesn't fit the
request/response model of serverless platforms. Run it:

- alongside the API on the same host (simplest: same Fly.io/Railway service
  or a second process), or
- on the agent's own machine, pointed at the production `DATABASE_URL`
  (works fine — it's just a DB client with a tool interface).

## Connecting the static frontend

The existing public page (`skill-exchange` artifact) ships with its catalog
baked in. At deploy time, swap its data source for API calls — no redesign
needed:

| Today (static) | After (live) |
|---|---|
| Baked-in skill list | `GET /api/v1/skills?q=&category=&sort=top` |
| Baked-in skill detail | `GET /api/v1/skills/{slug}` |
| Download button (embedded file) | `GET /api/v1/skills/{slug}/versions/latest` → verify signature client-side, then `POST /api/v1/installs` |
| "Submit skill" form (mailto/compose) | `POST /api/v1/accounts` once → `POST /api/v1/skills` with signature |
| Static ratings | `POST /api/v1/skills/{slug}/ratings` (needs API key) |

Keep the current page as the read path; add an account/key step only where
writing is involved.

## Environment variables

| Var | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string |
| `AUTO_APPROVE` | no | `true` skips moderation (dev only, default `false`) |
| `API_HOST` / `API_PORT` | no | Local dev listen address |

Secrets live in env, never in files. API keys are stored hashed; signing
private keys never leave the publisher's machine.

## Tests

`pytest tests/` — covers the signing round-trip, tamper rejection, wrong-key
rejection, and malformed-input handling. The DB layer is exercised via the
seed script against a local Postgres.
