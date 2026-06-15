"""Phase A — the hourly sessionization refresh job (Option B, Rishi 2026-06-13).

The one place the service touches the Patroni leader. The split that keeps the
chat node safe:

  1. Heavy half — read + group all ~3.4M `messages` into sittings — runs on the
     REPLICA via the analytics_ro pool (database.get_pool). Zero load on the
     chat primary. The 5s analytics_ro default is raised to a refresh-only
     window via SET LOCAL inside the read transaction.
  2. Tiny half — write the finished summary — runs on the LEADER via the
     analytics_rw pool (database.get_write_pool), in ONE transaction
     (DELETE + COPY) so a dashboard reader never sees an empty table.

A materialized-view REFRESH was rejected because it would do BOTH halves on the
leader. See db/analytics_sessions.sql for the data model + the SQL mirrored here.
"""

import logging

import config
import database

logger = logging.getLogger(__name__)

_COLUMNS = [
    "conversation_id",
    "session_index",
    "user_id",
    "influencer_id",
    "started_at",
    "ended_at",
    "user_turns",
    "bot_turns",
    "system_turns",
]

# Created by analytics_rw (it has CREATE on the analytics schema); the gated
# setup grants analytics_ro automatic SELECT so the dashboard can read it.
# Mirrored in db/analytics_sessions.sql.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS analytics.analytics_sessions (
    conversation_id VARCHAR(255) NOT NULL,
    session_index   INTEGER      NOT NULL,
    user_id         VARCHAR(255) NOT NULL,
    influencer_id   VARCHAR(255),
    started_at      TIMESTAMP    NOT NULL,
    ended_at        TIMESTAMP    NOT NULL,
    user_turns      INTEGER      NOT NULL,
    bot_turns       INTEGER      NOT NULL,
    system_turns    INTEGER      NOT NULL,
    PRIMARY KEY (conversation_id, session_index)
);
CREATE INDEX IF NOT EXISTS idx_analytics_sessions_user
    ON analytics.analytics_sessions (user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_analytics_sessions_started
    ON analytics.analytics_sessions (started_at);
CREATE INDEX IF NOT EXISTS idx_analytics_sessions_influencer
    ON analytics.analytics_sessions (influencer_id, started_at);
"""

# The heavy aggregation, run on the REPLICA. Returns finished session rows in
# _COLUMNS order. Mirrored as a comment in db/analytics_sessions.sql.
_SESSIONIZE_SQL = """
WITH ordered AS (
    SELECT m.conversation_id, c.user_id, c.influencer_id, m.role, m.created_at,
           m.created_at - LAG(m.created_at) OVER (
               PARTITION BY m.conversation_id ORDER BY m.created_at, m.id) AS gap
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE c.conversation_type = 'ai_chat'
),
flagged AS (
    SELECT *, CASE WHEN gap IS NULL OR gap > INTERVAL '20 minutes'
                   THEN 1 ELSE 0 END AS is_new_session
    FROM ordered
),
sessionized AS (
    SELECT *, SUM(is_new_session) OVER (
        PARTITION BY conversation_id ORDER BY created_at, role) AS session_index
    FROM flagged
)
SELECT conversation_id, session_index::int AS session_index, user_id, influencer_id,
       MIN(created_at) AS started_at, MAX(created_at) AS ended_at,
       COUNT(*) FILTER (WHERE role = 'user')      AS user_turns,
       COUNT(*) FILTER (WHERE role = 'assistant') AS bot_turns,
       COUNT(*) FILTER (WHERE role = 'system')    AS system_turns
FROM sessionized
GROUP BY conversation_id, session_index, user_id, influencer_id
"""


async def ensure_table(write_pool) -> None:
    # Idempotent CREATE — called once at startup so the table exists before the
    # service serves a single request (otherwise it's created only by the first
    # refresh, leaving a window where /headline hits a missing table). Fast.
    await write_pool.execute(_CREATE_TABLE_SQL)


async def refresh_sessions() -> int:
    """Recompute the session summary on the replica, then replace the leader
    table in one transaction. Returns the row count written."""
    read_pool = await database.get_pool()
    async with read_pool.acquire() as rconn:
        # Read-only transaction (the pool forces it); raise the heavy-scan
        # timeout for THIS transaction only, leaving the 5s default intact for
        # every user-facing read.
        async with rconn.transaction():
            # Server-side guard...
            await rconn.execute(
                f"SET LOCAL statement_timeout = '{config.SESSIONS_REFRESH_READ_TIMEOUT_SEC}s'"
            )
            # ...AND a matching per-call client timeout, or the pool's 30s
            # command_timeout kills this scan before the server timeout applies.
            rows = await rconn.fetch(
                _SESSIONIZE_SQL, timeout=config.SESSIONS_REFRESH_READ_TIMEOUT_SEC
            )

    write_pool = await database.get_write_pool()
    async with write_pool.acquire() as wconn:
        async with wconn.transaction():
            await wconn.execute(_CREATE_TABLE_SQL)
            await wconn.execute("DELETE FROM analytics.analytics_sessions")
            if rows:
                await wconn.copy_records_to_table(
                    "analytics_sessions",
                    schema_name="analytics",
                    records=rows,
                    columns=_COLUMNS,
                )
    logger.info("analytics_sessions refreshed: %d rows", len(rows))
    return len(rows)
