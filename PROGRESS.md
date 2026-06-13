# PROGRESS — yral-rishi-analytics

The checklist. Status: ✅ Done · ⏳ Pending · 🔄 In PR · 🔒 Gated on Rishi.
Phases mirror the design doc (`analytics-100x-vision.md` §9).

## Phase 0 — Stand up the service skeleton
| Item | Status |
|---|---|
| Repo scaffold (config / database / main / routes/health + __init__ markers) | ✅ Done |
| CLAUDE.md / README.md / PROGRESS.md / .gitignore | ✅ Done |
| requirements.txt (fastapi, uvicorn, asyncpg, redis, authlib, httpx) | ✅ Done |
| Dockerfile (non-root, slim, port 8001) | ✅ Done |
| CI workflow (lint + import-check) | ✅ Done |
| Swarm deploy spec (rishi-6 placement, resource caps, :stable, /healthz) | ✅ Done |
| `db/setup_analytics_ro.sql` written + reviewable (NOT executed) | ✅ Done |
| **Create GitHub remote + first push** | 🔒 Gated on Rishi |
| **Run `db/setup_analytics_ro.sql` on Patroni (pg_dump first)** | 🔒 Gated on Rishi |
| **`docker stack deploy` of the empty skeleton to rishi-6** | 🔒 Gated on Rishi |

Phase 0 build: complete (local). Privileged ops queued for Rishi.

## Phase A — Foundation + first signal
| Item | Status |
|---|---|
| `db/analytics_sessions.sql` — sessionization materialized view DDL | 🔄 In PR (authored + locally validated on PG16 synthetic data; NOT executed against any product DB) |
| `repositories/analytics_repo.py` — engaged sessions, second-msg, W1 return | 🔄 In PR (authored + validated; SELECT-only against the view) |
| Headline route (3 numbers, behind temporary shared-secret token) | 🔄 In PR (authored; `/headline?token=…`, plain tiles, retired in Phase B) |
| Hourly refresh loop (mirrors `_trending_stats_refresher`) | ⏳ Pending (blocked on refresh-connection question below) |

> **OPEN QUESTION for Rishi (blocks the refresh loop, not the DDL):** a
> materialized-view REFRESH is physically a *write*, and writes only land on
> the Patroni **leader**. But `database.py` opens a **replica-only, read-only**
> pool (Part C: "never the primary"). Design §3.4 says analytics writes its own
> small hourly `analytics`-schema objects **to the primary**. So the hourly
> refresh needs a narrow, clearly-scoped maintenance connection to the leader
> (used ONLY for `REFRESH ... analytics.analytics_sessions`, never for product
> reads). Need Rishi's call on: (a) confirm a leader maintenance connection is
> acceptable per §3.4, or (b) avoid materialization and compute sessions on-read
> over a bounded recent window. The view DDL above is valid either way.

## Phase B — Google login
| Item | Status |
|---|---|
| `analytics_login_audit` table (analytics schema) | ⏳ Pending |
| `auth.py` — Google OAuth, domain check, Redis session, audit | ⏳ Pending |
| `analytics.rishi.yral.com` Caddy stanza | 🔒 Gated on Rishi |

## Phases C–H
| Item | Status |
|---|---|
| C: The Glance (beautiful) | ⏳ Pending |
| D: Retention + depth views | ⏳ Pending |
| E: Bots + negative signals | ⏳ Pending |
| F: Drill-down chrome (metadata; raw transcripts held per §7.1) | ⏳ Pending |
| G: Coach funnel + economics | ⏳ Pending |
| H: Iterate (weekly) | ⏳ Pending |

Nothing merges to `main` until Rishi green-lights post-rollout stability.
