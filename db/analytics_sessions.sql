-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║  analytics_sessions.sql — the ONE new primitive: sessionization        ║
-- ║                                                                        ║
-- ║  The chat product has `conversations` (one per user-bot pair) and      ║
-- ║  `messages` (one row each) but NO concept of a "session" — a single    ║
-- ║  sitting of back-and-forth. Every engagement-quality metric needs one. ║
-- ║  This materialized view derives sessions from `messages` by a time     ║
-- ║  gap, and lives in the analytics schema the analytics_ro role OWNS —    ║
-- ║  so creating/refreshing it is a write to OUR schema, never to public.  ║
-- ║                                                                        ║
-- ║  Mirrors the chat repo's influencer_trending_stats pattern (a mat view ║
-- ║  + a background refresher) — SYMMETRY across projects.                 ║
-- ║                                                                        ║
-- ║  WHERE IT RUNS: this is the analytics schema (owned by analytics_ro),  ║
-- ║  so CREATE/REFRESH are legitimate writes. But a REFRESH is physically  ║
-- ║  a write, and writes only land on the Patroni LEADER — the read-only   ║
-- ║  replica pool in database.py cannot run it. The hourly refresh needs a ║
-- ║  narrow maintenance path; see the open question in PROGRESS.md /        ║
-- ║  the PR description. Nothing here is executed until Rishi's go +        ║
-- ║  the analytics_ro role/schema exist (db/setup_analytics_ro.sql).       ║
-- ╚══════════════════════════════════════════════════════════════════════╝

-- WITH NO DATA so CREATE is instant (no 5s statement_timeout risk on the
-- create itself). The first REFRESH is the heavy one — it scans messages
-- once with a window function. That refresh runs with a deliberately raised
-- statement_timeout (the 5s role default protects the user-facing read path,
-- not this hourly maintenance query).
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.analytics_sessions AS
WITH ordered AS (
    -- Every message in a bot conversation, tagged with the gap since the
    -- previous message in the SAME conversation. We sessionize over ALL
    -- messages (incl. proactive/nudge bot messages) so the timeline stays
    -- faithful — engagement metrics filter to role='user' downstream, and a
    -- proactive-only burst simply never clears the engaged bar (≥4 user msgs).
    SELECT
        m.conversation_id,
        c.user_id,
        c.influencer_id,
        m.role,
        m.created_at,
        m.created_at - LAG(m.created_at) OVER (
            PARTITION BY m.conversation_id ORDER BY m.created_at, m.id
        ) AS gap
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    -- Bot chats only. human_chat is a different product surface (creator
    -- takeover) and not part of the "falling in love with the bots" question.
    WHERE c.conversation_type = 'ai_chat'
),
flagged AS (
    -- A new session starts on a conversation's first message, or whenever the
    -- gap exceeds the 20-minute knob (design §3.2). 20, not 30: mobile users
    -- idle a lot and 30 would merge two genuine sittings. This literal is the
    -- SESSION_GAP_MINUTES knob — changing it is a view REBUILD (the gap is
    -- structural to what a "session" IS), unlike ENGAGED_MIN_USER_MSGS which
    -- is applied at read time and is the truly hot-editable knob.
    SELECT
        *,
        CASE WHEN gap IS NULL OR gap > INTERVAL '20 minutes' THEN 1 ELSE 0 END
            AS is_new_session
    FROM ordered
),
sessionized AS (
    -- Running sum of session-starts = a stable per-conversation session index.
    SELECT
        *,
        SUM(is_new_session) OVER (
            PARTITION BY conversation_id ORDER BY created_at, role
        ) AS session_index
    FROM flagged
)
SELECT
    conversation_id,
    session_index,
    user_id,
    influencer_id,
    MIN(created_at) AS started_at,
    MAX(created_at) AS ended_at,
    COUNT(*) FILTER (WHERE role = 'user')      AS user_turns,
    COUNT(*) FILTER (WHERE role = 'assistant') AS bot_turns,
    COUNT(*) FILTER (WHERE role = 'system')    AS system_turns
FROM sessionized
GROUP BY conversation_id, session_index, user_id, influencer_id
WITH NO DATA;

-- Required for REFRESH MATERIALIZED VIEW CONCURRENTLY (the hourly path that
-- doesn't block readers). One session is uniquely (conversation, index).
CREATE UNIQUE INDEX IF NOT EXISTS idx_analytics_sessions_pk
    ON analytics.analytics_sessions (conversation_id, session_index);

-- Cohort / return / per-user queries group by user; "today" and time-window
-- filters scan by start time; per-bot splits group by influencer.
CREATE INDEX IF NOT EXISTS idx_analytics_sessions_user
    ON analytics.analytics_sessions (user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_analytics_sessions_started
    ON analytics.analytics_sessions (started_at);
CREATE INDEX IF NOT EXISTS idx_analytics_sessions_influencer
    ON analytics.analytics_sessions (influencer_id, started_at);
