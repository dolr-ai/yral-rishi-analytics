-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  fix_login_audit_owner.sql — GATED one-time fix (Session 6 runs it)     ║
-- ║                                                                        ║
-- ║  Run ONCE on the Patroni LEADER with Rishi's go (pg_dump first).        ║
-- ║                                                                        ║
-- ║  analytics.analytics_login_audit was manually created as `postgres`,    ║
-- ║  so the analytics_rw role can neither manage (ensure_table) nor write   ║
-- ║  (audit INSERTs) it → /auth/google/callback hit permission/Undefined*   ║
-- ║  errors. Hand ownership to analytics_rw so the service owns its own      ║
-- ║  analytics-schema object.                                              ║
-- ║                                                                        ║
-- ║  INVARIANT going forward: every analytics-schema object is created AND  ║
-- ║  owned by analytics_rw (the service's ensure_table path), so this can't ║
-- ║  recur. No analytics object should be hand-created as postgres again.   ║
-- ╚══════════════════════════════════════════════════════════════════════╝

ALTER TABLE analytics.analytics_login_audit OWNER TO analytics_rw;

-- After this, analytics_rw can ensure_table (no-op) and INSERT audit rows;
-- analytics_ro keeps SELECT via the default-privilege grant in setup_analytics_ro.sql.
