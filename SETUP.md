# Skill Exchange — cheapest setup runbook ($0/month)

Everything is committed locally and ready. Your side is three accounts
(all free) and about 20 minutes of clicking.

## Costs

| Piece | Service | Cost |
|---|---|---|
| Code hosting | GitHub (public repo) | **$0** — public repos are free forever |
| Database | Supabase free tier | **$0** — 500 MB Postgres, plenty to start |
| API host | Render free tier | **$0** — sleeps when idle, wakes on request |
| CDN/DNS (optional) | Cloudflare | **$0** |
| **Total** | | **$0/mo** |

GitHub is free — public repos cost nothing, and private repos are free too.
No credit card needed for any of this.

## Your steps

### 1. GitHub — create the repo (5 min)

1. Sign up / log in at github.com
2. New repository → name it `skill-exchange` → Public → **don't** add a README
3. Tell Zuckbot the repo URL — he'll push the code up
   (he'll ask you for a personal access token through the secure vault;
   it only gets used once, for the push)

### 2. Supabase — the database (5 min)

1. Sign up / log in at supabase.com → **New project** (free tier)
2. Open the **SQL Editor**, paste the entire contents of
   `migrations/001_init.sql` from the repo, and run it
3. Go to **Project Settings → Database** and copy the **direct**
   Postgres connection string
   (`postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres`)
4. Give that string to Zuckbot — it goes into the API host as an env var,
   never into the code

### 3. Render — the API (10 min)

1. Sign up / log in at render.com → **New → Web Service**
2. Connect your `skill-exchange` GitHub repo
3. Settings: build `pip install -r requirements.txt`,
   start `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
4. Environment variables:
   - `DATABASE_URL` = the Supabase string from step 2
   - `AUTO_APPROVE` = `false`
5. Deploy (free tier). Your API is live at `https://<name>.onrender.com`

### 4. Seed the catalog (Zuckbot does this)

Once the API is up, Zuckbot runs `scripts/seed.py` against the Supabase
database to load the 5 starter skills, then verifies the whole flow:
publish → approve → install → rate.

## When to spend money

- **$0** works until you're genuinely busy.
- If the API sleeping on Render gets annoying: ~$7/mo keeps it always on,
  or move it to Fly.io (~$5/mo).
- If the database outgrows 500 MB: Supabase Pro is $25/mo.
- Realistic "it's actually popular" budget: **~$30/mo**.

## What's deliberately not done yet

- Wiring the public static page to the live API (30 min, after deploy)
- Signing-key rotation story (documented gap in SECURITY.md)
- The MCP server as a hosted endpoint (runs fine on the agent's own
  machine pointed at the production DB)
