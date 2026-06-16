"""Phase A — the sessionization refresh job (Option B + incremental).

The one place the service touches the Patroni leader. The split that keeps the
chat node safe:

  1. Read + group on the REPLICA via the analytics_ro pool (database.get_pool).
     Zero load on the chat primary.
  2. Write the finished summary on the LEADER via the analytics_rw pool
     (database.get_write_pool), in ONE transaction so a dashboard reader never
     sees a half-written table.

INCREMENTAL (Rishi 2026-06-16): a full recompute reads ALL ~3.4M messages and
sorts them for the window functions every run — EXPLAIN confirmed an
index-driven plan (no seq-scan to fix), but the cost is inherent and we can't
add an index to the product `messages` table (Part C). So each run recomputes
ONLY the conversations with a message newer than the high-water mark
(max(ended_at) already in the summary), then replaces just those rows. The
first run on an empty table does the one-time full build. This bounds steady-
state work to recently-active conversations instead of the whole corpus.

Cancellation is clean: the server-side `SET LOCAL statement_timeout` is the real
guard (a clean QueryCanceledError, connection stays usable); the asyncpg
client-side timeout is set strictly ABOVE it so the server always cancels first
— we never let the client timeout close the connection mid-transaction (which
surfaced as InterfaceError). See db/analytics_sessions.sql for the data model.
"""

import asyncio
import logging
from datetime import timedelta

from asyncpg.exceptions import QueryCanceledError

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

# The aggregation, run on the REPLICA. Returns finished session rows in
# _COLUMNS order. $1 is the conversation-id scope: NULL → every ai_chat
# conversation (full build); a text[] → only those conversations (incremental).
# When scoped, we still read each conversation's FULL history so its session
# boundaries are computed correctly. Mirrored as a comment in
# db/analytics_sessions.sql.
_SESSIONIZE_SQL = """
WITH ordered AS (
    SELECT m.conversation_id, c.user_id, c.influencer_id, m.role, m.created_at,
           m.created_at - LAG(m.created_at) OVER (
               PARTITION BY m.conversation_id ORDER BY m.created_at, m.id) AS gap
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    WHERE c.conversation_type = 'ai_chat'
      AND ($1::text[] IS NULL OR m.conversation_id = ANY($1))
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


async def _changed_conversations(read_pool, watermark) -> list[str]:
    # Conversations with a message newer than the high-water mark, minus a small
    # lookback buffer to absorb replica lag / equal-timestamp ties (re-processing
    # a few recent conversations is harmless — the recompute is idempotent).
    # Uses idx_messages_created_at, so it scans only recent messages.
    cutoff = watermark - timedelta(minutes=config.SESSIONS_REFRESH_LOOKBACK_MIN)
    rows = await read_pool.fetch(
        "SELECT DISTINCT conversation_id FROM messages WHERE created_at > $1",
        cutoff,
    )
    return [r["conversation_id"] for r in rows]


async def refresh_sessions() -> int:
    """Recompute sessions on the replica and replace the affected rows on the
    leader. Incremental: only conversations changed since the high-water mark
    (full build when the summary is empty). Returns the row count written, or
    0 when nothing changed / the read was cancelled by the timeout guard."""
    read_pool = await database.get_pool()

    # High-water mark = latest message we've already sessionized. NULL → the
    # summary is empty → one-time full build.
    watermark = await read_pool.fetchval(
        "SELECT max(ended_at) FROM analytics.analytics_sessions"
    )
    if watermark is None:
        scope = None  # full
    else:
        scope = await _changed_conversations(read_pool, watermark)
        if not scope:
            logger.info("sessions refresh: nothing changed since %s", watermark)
            return 0

    server_sec = config.SESSIONS_REFRESH_READ_TIMEOUT_SEC
    try:
        async with read_pool.acquire() as rconn:
            async with rconn.transaction():
                # Server-side timeout is the REAL guard (clean cancel). The
                # client timeout sits strictly above it so the server always
                # cancels first — never the client closing the conn mid-txn.
                await rconn.execute(f"SET LOCAL statement_timeout = '{server_sec}s'")
                rows = await rconn.fetch(
                    _SESSIONIZE_SQL, scope, timeout=server_sec + 30
                )
    except (QueryCanceledError, asyncio.TimeoutError):
        logger.warning(
            "sessions refresh cancelled by the %ss timeout (scope=%s); will retry",
            server_sec,
            "full" if scope is None else f"{len(scope)} convs",
        )
        return 0

    write_pool = await database.get_write_pool()
    async with write_pool.acquire() as wconn:
        async with wconn.transaction():
            await wconn.execute(_CREATE_TABLE_SQL)
            if scope is None:
                await wconn.execute("DELETE FROM analytics.analytics_sessions")
            else:
                await wconn.execute(
                    "DELETE FROM analytics.analytics_sessions "
                    "WHERE conversation_id = ANY($1)",
                    scope,
                )
            if rows:
                await wconn.copy_records_to_table(
                    "analytics_sessions",
                    schema_name="analytics",
                    records=rows,
                    columns=_COLUMNS,
                )
    logger.info(
        "analytics_sessions refreshed: scope=%s, %d rows",
        "full" if scope is None else f"{len(scope)} convs",
        len(rows),
    )
    return len(rows)
