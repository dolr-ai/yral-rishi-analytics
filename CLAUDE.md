# yral-rishi-analytics

Read-only internal analytics service at `analytics.rishi.yral.com`. Answers one
root question: **"is anyone falling in love with talking to these bots?"** It
**reads** the chat product's database — it is a reader, never an emitter, never
a second source of truth.

## Architecture
Team member → Caddy (rishi-1/2) → This FastAPI service (rishi-6, beside Langfuse)
→ Patroni Postgres **read replica** (read-only `analytics_ro` role).
One service. Its own `analytics` schema for derived data. Pulls from the chat
product DB; never writes to `public`.

## Code pattern (mirrors the chat service for SYMMETRY)
- app/config.py: `_env()` reads os.environ. All settings as module constants.
- app/database.py: lazy asyncpg pool. `get_pool()` on first call. Opened
  read-only (`default_transaction_read_only=on`) against a replica.
- app/auth.py (Phase B): Google Workspace OAuth, restricted to @gobazzinga.io.
- app/routes/: one file per view-group. Router with prefix.
- app/repositories/: one file per query-group. Raw read-only SQL via asyncpg.

## Rules
1. SYMMETRY: every route file has the same shape; every repo file has the same
   shape. The codebase reads like the chat service even though it's separate.
2. Comments explain WHY not WHAT. No line-by-line narration.
3. English-readable names; common coding abbreviations are fine (id, url, api,
   http, json, sql, utc, db, env, config, sse, jwt, dto, etl, ci, cd, pr, auth).
4. Simplicity first. If >100 lines of new LOGIC, stop and check with Rishi.
   HTML/CSS templates are exempt — but CALL OUT template line counts per PR.
5. No new event pipeline, no warehouse, no SPA/build step. The chat DB is the
   event log; we render server-side HTML strings + inline-SVG sparklines.
6. Honest about small samples: sample-size badge on every number; "too early
   to trust" instead of a fake trend below SMALL_SAMPLE_THRESHOLD.
7. When unsure, ask. A question beats undoing a mistake — real users are on v2.

## HARD SAFETY RULES (non-negotiable — the firewall around the chat service)
The agent v2 chat service serves real users. It must NEVER go down or degrade
because of anything analytics does. Treat any temptation to bend these as a
stop-and-ask-Rishi event.

- **DB: replica reads, one tiny leader write.** Dashboard + heavy reads use
  the `analytics_ro` role on a Patroni **read replica** (SELECT on `public`,
  read-only, 5s statement_timeout). The **sole** contact with the Patroni
  **leader** is the hourly refresh job's small write into the `analytics`
  schema via the new `analytics_rw` role — never a heavy read on the leader
  (all heavy reads run on the replica via analytics_ro), never any write to
  `public`. The ONLY writes analytics may do, on either node, are to its own
  `analytics` schema (Option B, Rishi 2026-06-13).
- **No migrations against product tables. Ever.** The sole DDL allowed is
  `db/setup_analytics_ro.sql`, run ONCE with Rishi's explicit go, pg_dump first.
- **Never touch the yral-rishi-agent chat service.** No edits to its repo, no
  `docker service update/rm/scale/restart` against it, no redeploy/rollback of
  it, no Caddy edits affecting `agent.rishi.yral.com`. Reading its repo for
  schema/context is fine.
- **Never touch rishi-1/2/3** (chat-ai prod) at all.
- **Isolation by construction.** Own Swarm service on rishi-6, own image, role,
  secrets, resource caps, `/healthz`, `:stable` rollback tag. Breaking analytics
  must have ZERO effect on v2. If we retire it, `docker service rm` it
  deliberately (avoid the past "zombie services" failure mode).
- **No secrets in git.** DB creds + Google OAuth secret go to Swarm secrets
  out-of-band.
- **Privileged ops gated on Rishi (per-action, named):** running
  `db/setup_analytics_ro.sql`, creating the GitHub remote + first push, the
  `analytics.rishi.yral.com` Caddy stanza, any `docker stack deploy`.

## Agent / git rules
- Feature branches only; never push to `main`. Date in the branch name.
- `git branch --show-current` right after checkout AND before each commit
  (this project has a branch-collision history).
- One PR per concern; under ~400 lines; the >100-logic-lines checkpoint applies.
- Nothing merges to `main` until Rishi green-lights post-rollout stability.

## Reading order
1. This file → 2. docs design (analytics-100x-vision.md in the chat repo) →
3. app/config.py → 4. app/database.py → 5. app/main.py → 6. app/routes/health.py
