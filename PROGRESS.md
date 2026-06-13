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
| `analytics_sessions` view in `analytics` schema + hourly refresh loop | ⏳ Pending (blocked: needs the gated DB role/schema) |
| `repositories/analytics_repo.py` — engaged sessions, second-msg, W1 return | ⏳ Pending |
| Headline route (3 numbers, behind temporary shared-secret token) | ⏳ Pending |

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
