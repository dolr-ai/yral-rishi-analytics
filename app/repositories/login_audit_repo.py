"""Phase B — durable login-audit writes/reads (analytics.analytics_login_audit).

Same shape as the chat repos (module-level async fns, raw SQL). Writes go to
the LEADER via the analytics_rw pool (a login attempt is a small, infrequent
write to our own analytics schema — the §6.3 durable audit). Reads (a future
audit view) come off the replica via analytics_ro. See db/analytics_login_audit.sql.
"""

# Mirrored in db/analytics_login_audit.sql. Created by analytics_rw on the
# leader (it has CREATE on the analytics schema).
_CREATE_TABLE_SQL = """
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
"""


async def ensure_table(write_pool) -> None:
    # Idempotent; called once at startup when the rw pool is available.
    await write_pool.execute(_CREATE_TABLE_SQL)


async def record_attempt(
    write_pool,
    email: str | None,
    domain: str | None,
    allowed: bool,
    ip: str | None,
    user_agent: str | None,
) -> None:
    """Log one login attempt (success OR reject) to the leader. Never raises
    into the auth flow's happy path — the caller treats an audit-write failure
    as non-fatal (a login shouldn't break because the audit row didn't land),
    but it logs the failure."""
    await write_pool.execute(
        """
        INSERT INTO analytics.analytics_login_audit
            (email, domain, allowed, ip, user_agent)
        VALUES ($1, $2, $3, $4, $5)
        """,
        email,
        domain,
        allowed,
        ip,
        user_agent,
    )


async def recent(read_pool, limit: int = 100) -> list[dict]:
    rows = await read_pool.fetch(
        """
        SELECT attempted_at, email, domain, allowed, ip, user_agent
        FROM analytics.analytics_login_audit
        ORDER BY attempted_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]
