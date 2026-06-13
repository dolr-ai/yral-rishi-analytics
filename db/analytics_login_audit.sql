-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  analytics_login_audit — durable record of every team login attempt    ║
-- ║                                                                        ║
-- ║  REFERENCE / DOCUMENTATION (mirrored in app/repositories/login_audit_  ║
-- ║  repo.py, which is what runs). The service creates this in the          ║
-- ║  analytics schema via analytics_rw (CREATE on the schema); analytics_ro ║
-- ║  reads it for a future audit view via the default-privilege SELECT.    ║
-- ║                                                                        ║
-- ║  WHY Postgres, not Redis: sessions are ephemeral (Redis — a blip just   ║
-- ║  forces re-login), but the audit must survive restarts and be queryable ║
-- ║  forever (design §6.3). Every attempt — allowed AND denied — is logged. ║
-- ╚══════════════════════════════════════════════════════════════════════╝

CREATE TABLE IF NOT EXISTS analytics.analytics_login_audit (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempted_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    email        TEXT,
    domain       TEXT,
    allowed      BOOLEAN NOT NULL,
    ip           TEXT,
    user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS idx_login_audit_attempted_at
    ON analytics.analytics_login_audit (attempted_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_audit_email
    ON analytics.analytics_login_audit (email);
