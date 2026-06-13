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
| `analytics.analytics_sessions` — service-refreshed TABLE (was a mat view) | 🔄 In PR (authored + locally validated on PG16 synthetic data; NOT executed) |
| `repositories/analytics_repo.py` — engaged sessions, second-msg, W1 return | 🔄 In PR (authored + validated; SELECT-only against the table) |
| Headline route (3 numbers, behind temporary shared-secret token) | 🔄 In PR (authored; `/headline?token=…`, plain tiles, retired in Phase B) |
| Hourly refresh job — replica read → leader write (`services/sessions_refresh.py`) | 🔄 In PR (authored; dormant until `ANALYTICS_DB_DSN_RW` exists) |
| `analytics_rw` role added to `db/setup_analytics_ro.sql` | 🔄 In PR (gated; runs once with Rishi's go) |

> **RESOLVED — Rishi 2026-06-13, Option B (replica-crunch, leader-write).** The
> hourly summary is a regular **table**, not a materialized view: the heavy
> ~3.4M-row aggregation runs on the **replica** (`analytics_ro`, 5s timeout
> raised to 60s for that one scan via `SET LOCAL`); only the small finished
> result is written to the **leader** in one transaction via the new
> **`analytics_rw`** role (60s timeout, writes the `analytics` schema only, zero
> `public` access). A mat-view REFRESH was rejected — it would run both halves
> on the chat primary. The refresher stays dormant until `ANALYTICS_DB_DSN_RW`
> is provisioned, so there's no leader contact until then. **The complete gated
> setup script (with `analytics_rw`) lives on this Phase A branch.**

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
