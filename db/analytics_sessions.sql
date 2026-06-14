-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  analytics_sessions — the ONE new primitive: sessionization            ║
-- ║                                                                        ║
-- ║  REFERENCE / DOCUMENTATION. This is NOT a gated script you run by hand. ║
-- ║  The analytics service creates and refreshes this table itself, hourly,║
-- ║  via the analytics_rw role (leader, write-only to the analytics schema).║
-- ║  The executable copies of both statements below live as constants in    ║
-- ║  app/services/sessions_refresh.py — keep the two in sync. This file     ║
-- ║  exists so the data model + the sessionization logic are reviewable as  ║
-- ║  plain SQL.                                                             ║
-- ║                                                                        ║
-- ║  WHY a regular TABLE, not a MATERIALIZED VIEW (Rishi, 2026-06-13):      ║
-- ║  a leader-side `REFRESH MATERIALIZED VIEW` runs BOTH the heavy 3.4M-row ║
-- ║  scan AND the write on the chat primary every hour — exactly the load   ║
-- ║  Part C exists to keep off the chat node. Instead the refresh job runs  ║
-- ║  the heavy aggregation as a SELECT on the REPLICA (analytics_ro), then   ║
-- ║  writes only the small finished result to this table on the LEADER      ║
-- ║  (analytics_rw), in one transaction so readers never see an empty table.║
-- ╚══════════════════════════════════════════════════════════════════════╝

-- The durable summary: one row per (conversation, session). Created by the
-- service via analytics_rw (which has CREATE on the analytics schema); the
-- gated setup grants analytics_ro automatic SELECT on it for dashboard reads.
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

-- The aggregation the refresh job runs ON THE REPLICA (analytics_ro). It reads
-- public.messages/conversations read-only and returns the finished session
-- rows in the column order above. The 5s analytics_ro default timeout is
-- raised to a refresh-only window (SET LOCAL) for this one heavy scan; the
-- 5s cap still protects every user-facing read.
--
--   WITH ordered AS (
--       -- every bot-conversation message tagged with the gap since the prev
--       -- message in the same conversation. We sessionize over ALL messages
--       -- (incl. proactive/nudge) so the timeline is faithful; engagement
--       -- metrics filter to role='user' downstream.
--       SELECT m.conversation_id, c.user_id, c.influencer_id, m.role, m.created_at,
--              m.created_at - LAG(m.created_at) OVER (
--                  PARTITION BY m.conversation_id ORDER BY m.created_at, m.id) AS gap
--       FROM messages m JOIN conversations c ON c.id = m.conversation_id
--       WHERE c.conversation_type = 'ai_chat'      -- bot chats only
--   ),
--   flagged AS (
--       -- a new session starts on the first message or when the gap exceeds
--       -- the 20-minute knob (structural; changing it is a full recompute)
--       SELECT *, CASE WHEN gap IS NULL OR gap > INTERVAL '20 minutes'
--                      THEN 1 ELSE 0 END AS is_new_session FROM ordered
--   ),
--   sessionized AS (
--       SELECT *, SUM(is_new_session) OVER (
--           PARTITION BY conversation_id ORDER BY created_at, role) AS session_index
--       FROM flagged
--   )
--   SELECT conversation_id, session_index::int, user_id, influencer_id,
--          MIN(created_at) AS started_at, MAX(created_at) AS ended_at,
--          COUNT(*) FILTER (WHERE role='user')      AS user_turns,
--          COUNT(*) FILTER (WHERE role='assistant') AS bot_turns,
--          COUNT(*) FILTER (WHERE role='system')    AS system_turns
--   FROM sessionized
--   GROUP BY conversation_id, session_index, user_id, influencer_id;
