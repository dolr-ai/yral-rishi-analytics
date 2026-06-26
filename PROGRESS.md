# PROGRESS — yral-rishi-analytics

The checklist. Status: ✅ Done · ⏳ Pending · 🔄 In PR · 🔒 Gated on Rishi.
Phases mirror the design doc (`analytics-100x-vision.md` §9).

## Real-time events pipeline — analytics-events-consumer (2026-06-26, PR #10)
Mobile → Saikat's Strimzi Kafka **bridge** → OUR consumer → OUR ClickHouse.
A separate Swarm service (same image, `uvicorn consumer_main:app`).
| Piece | Status |
|---|---|
| `services/kafka_bridge.py` — httpx Strimzi client (create/subscribe/poll/commit; public-base URLs; 404→recreate) | 🔄 In PR (validated vs mock transport) |
| `services/snowplow.py` — enriched-TSV parse (snowplow_analytics_sdk) → typed cols + JSON | 🔄 In PR (validated) |
| `services/clickhouse_sink.py` — `raw_events` ReplacingMergeTree + batched insert | 🔄 In PR (schema+dedup validated via chdb) |
| `consumer_main.py` — poll→parse→batch(1000/5s)→insert→**commit-after-write**; `/health`; Sentry | 🔄 In PR (loop + insert-before-commit validated) |
| `deploy/events-consumer-stack.yml` (1 replica, data-plane, rishi-6, secrets) | 🔄 In PR |
| `.github/workflows/deploy-events-consumer.yml` — Vault-OIDC token → Swarm secret | 🔄 In PR |
| ClickHouse `analytics_consumer` write user + DSN secret | 🔒 Coordinator (on deploy) |
| Build/push image + `docker stack deploy` + CH-user create | 🔒 Coordinator |

> **Not validated locally (no runnable CH server on this box):** the
> clickhouse-connect async **insert against a live server** (marshaling
> datetime/Nullable/String). Schema + dedup proven via chdb; live insert is
> confirmed by the coordinator against prod ClickHouse on deploy. JSON stored as
> `String` (CH 24.3 has no stable JSON type) — query via `JSONExtract*`.


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
| `docs/RUNBOOK.md` + password-substitution + both-roles-on-main notes | 🔄 In PR |
| `analytics_login_audit` table + `login_audit_repo.py` (B1) | 🔄 In PR (validated locally) |
| `auth.py` — Google OAuth, domain check, Redis session, audit (B2) | 🔄 In PR (authored; **UNVERIFIED** — needs OAuth client + Redis) |
| Temp `/headline` token retired (auto, once auth configured) | 🔄 In PR |
| Google OAuth client (consent screen Internal + Web client + redirect URI) | 🔒 Gated on Rishi (create at Phase B) |
| Swarm secrets: `analytics_session_secret`, `analytics_google_oauth_client_secret` | 🔒 Gated on Rishi |
| `analytics.rishi.yral.com` Caddy stanza | 🔒 Gated on Rishi |

> **B2 is authored but UNVERIFIED** — the OAuth + Redis-session paths can't run
> until Rishi creates the Google OAuth client and Redis is reachable. Auth stays
> **dormant** until the OAuth client id/secret + `analytics_session_secret` are
> provisioned; until then `/headline` uses the temp token (validated locally).
> The token retires automatically the moment auth goes live (no flag flip).

## Post-first-signal patches (21γ)
| Item | Status |
|---|---|
| P19 — `HEADLINE_TOKEN` file-first (drop `--env-add` workaround) | ✅ Merged (#4 → main) |
| P20 — startup table-ensure + `/headline` "warming up" (no 500) | ✅ Merged (#4 → main) |

## Refresher proper fix + audit ownership (2026-06-16, PR #9)
| Item | Fix | Status |
|---|---|---|
| Refresher full-recompute too slow (timed out hourly) | **incremental watermark sessionization** — recompute only conversations changed since `max(ended_at)`; full build only when empty. EXPLAIN showed an index-driven plan (no seq-scan to fix) + can't index the product `messages` table → incremental is the fix | 🔄 In PR (5.3s full → 2.8ms scoped, local 3.4M repro) |
| Timeout surfaced as InterfaceError | server `SET LOCAL` is the guard; client timeout sits ABOVE it so the server cancels first (clean `QueryCanceledError`); caught + retried | 🔄 In PR |
| `login_audit` owned by `postgres` → `analytics_rw` can't write | gated `db/fix_login_audit_owner.sql` (`ALTER TABLE … OWNER TO analytics_rw`, Session 6 runs) + invariant: all analytics objects owned by `analytics_rw` | 🔒 Gated (validated: before=permission denied, after=insert ok) |

> **Done with the §3.5 follow-up:** the full hourly recompute is replaced by
> incremental. Steady-state work is now bounded to recently-active conversations.

## Sentinel auth + refresh timeout (2026-06-15, PR #8)
| Gap | Fix | Status |
|---|---|---|
| Sentinel-level AUTH broke discovery (login 500) | master-ONLY auth — drop `sentinel_kwargs` password, keep `master_for(password=)` | 🔄 In PR (validated vs master-only-auth Sentinel) |
| Refresher killed by pool's 30s client `command_timeout` (asyncpg.TimeoutError) | per-call `fetch(timeout=)` + matching `SET LOCAL`, both from `SESSIONS_REFRESH_READ_TIMEOUT_SEC=120` | 🔄 In PR |

> **Tracked follow-up (NOT building yet):** replace the full hourly 3.4M-row
> recompute with **incremental, watermark-based sessionization** (only
> new/changed messages since the last refresh). A full recompute every hour
> won't scale; design §3.5 flagged ~30s refresh as the trigger to go incremental.

## Google-login flip gaps (2026-06-14)
Four repo-side gaps surfaced by the first flip attempt (auth reverted to dormant):
| Gap | Fix | Status |
|---|---|---|
| Redis AUTH missing → sessions can't store | `session_store` passes `REDIS_PASSWORD` to Sentinel + master_for; secret mounted in `stack.yml` | 🔄 In PR (validated vs throwaway auth Sentinel) |
| Wrong Sentinel master name | `REDIS_SENTINEL_MASTER` default → `yral-v2-redis-primary` | 🔄 In PR |
| `login_audit` never created (OAuth callback 500) | startup ensure now activated by the gap-4 fix; each ensure independently guarded | 🔄 In PR |
| `ANALYTICS_DB_DSN_RW` case-mismatch froze the refresher | `config` reads DSNs via case-robust `_secret()`; gate now agrees with the reader | 🔄 In PR (validated) |

## Phases C–H
| Item | Status |
|---|---|
| C: The Glance (beautiful) | ⏳ Pending |
| **D / View 1: Comeback Curve (cohort retention grid)** | 🔄 In PR (full stack: `retention_repo` + `/retention`; validated locally) |
| D: depth views (still-here-at-N, return-to-same-bot) | ⏳ Pending (after first signal informs) |
| E: Bots + negative signals | ⏳ Pending |
| F: Drill-down chrome (metadata; raw transcripts held per §7.1) | ⏳ Pending |
| G: Coach funnel + economics | ⏳ Pending |
| H: Iterate (weekly) | ⏳ Pending |

Full stack owned by one session (queries + server-rendered HTML); no separate
Frontend session. Deeper D–G views build informed by the live numbers.
Nothing merges to `main` until Rishi green-lights post-rollout stability.
