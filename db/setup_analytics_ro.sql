-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  setup_analytics_ro.sql — PRIVILEGED, DO NOT EXECUTE FROM THIS SESSION ║
-- ║                                                                        ║
-- ║  Run ONLY with Rishi's explicit per-action go, pg_dump snapshot first  ║
-- ║  (CLAUDE.md Rule 9), routed via Session 6. This is the ONE DDL the     ║
-- ║  analytics project is allowed to run against the product cluster, and  ║
-- ║  it runs exactly once. It creates a read-only role + a separate        ║
-- ║  `analytics` schema; it grants ZERO write/DDL on `public`.             ║
-- ║                                                                        ║
-- ║  Run against the Patroni LEADER (role + schema are cluster-global and  ║
-- ║  replicate to the replicas the service actually reads from).           ║
-- ╚══════════════════════════════════════════════════════════════════════╝
--
-- Connect to the `yral_agent_db` database before running (the grants apply
-- within that DB's connection; this script intentionally carries no \connect
-- line so the operator chooses the target explicitly).
--
-- PASSWORDS: run this script as-is (placeholder passwords), then immediately,
-- in the same psql session, set the real ones —
--     ALTER ROLE analytics_ro PASSWORD '<real-ro>';
--     ALTER ROLE analytics_rw PASSWORD '<real-rw>';
-- and store the full DSNs in Swarm secrets analytics_db_dsn / analytics_db_dsn_rw.
-- Prefer ALTER ROLE-after over sed-substituting this file so a real password
-- never lands on disk or in shell history. See docs/RUNBOOK.md §1.

-- 1. The read-only role the analytics service logs in as.
--    Password is a PLACEHOLDER — replace at run time with a real secret and
--    store it ONLY in Swarm secrets (analytics_db_dsn), never in git.
CREATE ROLE analytics_ro LOGIN PASSWORD 'REPLACE_ME_VIA_SWARM_SECRET';

-- 2. Force every transaction this role opens to be read-only. Belt: the
--    service's pool also sets default_transaction_read_only=on (database.py).
ALTER ROLE analytics_ro SET default_transaction_read_only = on;

-- 3. Postgres kills any query this role runs that exceeds 5s — a runaway
--    analytics scan can never load the cluster (Session 6 nit, 2026-06-13).
ALTER ROLE analytics_ro SET statement_timeout = '5s';

-- 4. The analytics service's OWN schema for its derived data (sessionization
--    view, login audit, cached aggregates). It owns this; it writes only here,
--    never to `public`.
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION analytics_ro;

-- 5. SELECT-only on the product (`public`) tables. No INSERT/UPDATE/DELETE/
--    ALTER/DROP/TRUNCATE — enforced at the grant level, not by convention.
GRANT USAGE ON SCHEMA public TO analytics_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analytics_ro;

-- 6. Future product tables also become readable automatically (so a new chat
--    table doesn't silently break an analytics view). Still SELECT-only.
--    All 27 current public tables are owned by `postgres`, and future
--    migrations also run as `postgres` (verified 2026-06-13 by Session 6 via
--    \dt+), so the default privilege is scoped explicitly FOR ROLE postgres.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    GRANT SELECT ON TABLES TO analytics_ro;

-- ── analytics_rw — the write-only leader role (refresh job ONLY) ──────────
-- The hourly refresh reads the heavy 3.4M-row aggregation on the REPLICA via
-- analytics_ro, then writes the small finished result to analytics.* on the
-- LEADER via this role. It has ZERO access to `public` (the heavy read never
-- runs as analytics_rw) — its only reach into the cluster is writing our own
-- analytics schema. Decided by Rishi 2026-06-13 (replica-crunch, leader-write).

-- 7. The role. Password is a PLACEHOLDER — real secret goes to Swarm
--    (analytics_db_dsn_rw), never git. NOT read-only (it must write).
CREATE ROLE analytics_rw LOGIN PASSWORD 'REPLACE_ME_VIA_SWARM_SECRET';

-- 8. A 60s ceiling — generous enough for the hourly DELETE+COPY of the
--    finished summary, still a hard backstop against a runaway write.
ALTER ROLE analytics_rw SET statement_timeout = '60s';

-- 9. Write within the analytics schema only. CREATE lets the service create
--    its own derived tables (e.g. analytics_sessions) on first refresh.
GRANT USAGE, CREATE ON SCHEMA analytics TO analytics_rw;
GRANT INSERT, UPDATE, DELETE, TRUNCATE, SELECT
    ON ALL TABLES IN SCHEMA analytics TO analytics_rw;

-- 10. Tables analytics_rw creates in the analytics schema become readable by
--     analytics_ro automatically — that's how the dashboard (read replica)
--     sees the refreshed summary.
ALTER DEFAULT PRIVILEGES FOR ROLE analytics_rw IN SCHEMA analytics
    GRANT SELECT ON TABLES TO analytics_ro;

-- Deliberately NOT granted: any write or DDL on `public` for EITHER role;
-- analytics_rw gets NO `public` access at all (USAGE or SELECT); CREATEDB;
-- CREATEROLE; SUPERUSER; access to other schemas (langfuse, etc.). If a future
-- analytics view needs llm_costs / coach_messages, those are in `public` and
-- already covered by analytics_ro's SELECT grant above (read on the replica).
