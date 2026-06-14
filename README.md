# yral-rishi-analytics

Read-only internal analytics service for the `yral-rishi-agent` chat product.
Answers one root question: **"is anyone falling in love with talking to these
bots?"** — operationalised as *do engaged users voluntarily come back and go
deeper?* (W1 return rate + flattening cohort retention as the spine).

Full design: `docs/designs/analytics-100x-vision.md` in the chat repo.

## What it is
- A separate FastAPI service, its own repo, deployed as a Docker Swarm service
  **pinned to rishi-6** (beside Langfuse), on `analytics.rishi.yral.com`.
- A **reader** of the chat product's Postgres — read-only `analytics_ro` role,
  pointed at a **Patroni read replica**, with its own `analytics` schema for
  derived data (sessionization view, login audit, cached aggregates).
- Server-rendered HTML + inline-SVG sparklines. No SPA, no build step, no new
  event pipeline. Google Workspace login (@gobazzinga.io) — Phase B.

## Why a separate service
Isolation. A heavy query or a bug in analytics must never be able to touch the
chat path during or after the 10% rollout. See CLAUDE.md "HARD SAFETY RULES".

## Lifecycle (anti-zombie-service)
Single named owner. Documented here + in PROGRESS.md. `/healthz` watched by the
cluster. `:stable` rollback tag. A line in the daily email digest so it can
never run silently-broken. If retired, `docker service rm` it deliberately.

## Local development
```
cd app
pip install -r ../requirements.txt
ANALYTICS_DB_DSN="postgresql://analytics_ro:...@<replica>:5432/postgres" \
  uvicorn main:app --host 0.0.0.0 --port 8001
```
`GET /healthz` returns `{"status":"ok","database":"reachable"}` when the
read-only replica pool is up.

## Layout
```
app/config.py         env constants (DB DSN, knobs, OAuth placeholders)
app/database.py       lazy read-only asyncpg pool (replica)
app/main.py           FastAPI app + lifespan
app/routes/health.py  GET /healthz + DB-reachable check
app/routes/           one file per view-group
app/repositories/     one file per query-group (raw read-only SQL)
db/setup_analytics_ro.sql   PRIVILEGED, gated, run-once DDL (role + schema)
deploy/stack.yml      Swarm spec (rishi-6 placement, resource caps, :stable)
Dockerfile, .github/workflows/ci.yml
```

## Tunable knobs (hot-editable via env / Swarm)
- `SESSION_GAP_MINUTES` (default 20) — gap that starts a new session.
- `ENGAGED_MIN_USER_MSGS` (default 4) — user-message bar for an engaged session.
- `SMALL_SAMPLE_THRESHOLD` (default 30) — below this, "too early to trust".

## Status
Phase 0 (scaffold) — see PROGRESS.md. Not deployed. DB role/schema not created.
