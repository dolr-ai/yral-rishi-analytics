"""Phase A — the three headline read queries.

All read-only SELECTs against the analytics.analytics_sessions materialized
view (db/analytics_sessions.sql). Same shape as the chat repo's repositories
(module-level async fns taking `pool`, raw SQL) — SYMMETRY across projects.

The ENGAGED_MIN_USER_MSGS threshold is applied HERE at read time, not baked
into the view — so it stays a hot-editable knob (change the env var, no view
rebuild). The 20-min session gap, by contrast, is structural to the view.

Every number carries its sample size (`n`) so the dashboard can show an
honest "too early to trust" badge below SMALL_SAMPLE_THRESHOLD (design §8).
Timestamps in `messages` are naive UTC; we compare against UTC wall-clock.
"""

import config


async def engaged_sessions_today(pool) -> dict:
    """Count of engaged sessions (≥ ENGAGED_MIN_USER_MSGS user turns) that
    started today (UTC). THE "is today alive" pulse on the Glance view. Its
    own count is its sample size."""
    count = await pool.fetchval(
        """
        SELECT count(*)
        FROM analytics.analytics_sessions
        WHERE user_turns >= $1
          AND started_at >= date_trunc('day', now() AT TIME ZONE 'utc')
          AND started_at <  date_trunc('day', now() AT TIME ZONE 'utc')
                            + interval '1 day'
        """,
        config.ENGAGED_MIN_USER_MSGS,
    )
    return {"count": count or 0, "n": count or 0}


async def second_message_rate(pool, since_days: int = 7) -> dict:
    """Of first sittings (session_index = 1) begun in the last `since_days`,
    what fraction got a SECOND user message. The leading indicator — whether
    a first-time contact goes past one message (design §1.2). Denominator is
    the count of first contacts = the sample size."""
    row = await pool.fetchrow(
        """
        SELECT
            count(*) FILTER (WHERE user_turns >= 2) AS with_second,
            count(*)                                AS total
        FROM analytics.analytics_sessions
        WHERE session_index = 1
          AND started_at >= (now() AT TIME ZONE 'utc')
                            - make_interval(days => $1)
        """,
        since_days,
    )
    total = row["total"] or 0
    with_second = row["with_second"] or 0
    return {
        "rate": (with_second / total) if total else None,
        "numerator": with_second,
        "n": total,
    }


async def w1_return_rate(pool) -> dict:
    """THE love number, reconciled to the cohort grid (View 1) — the source of
    truth. Of newly-engaged users (first engaged session 7-21 days ago, so the
    window has fully elapsed), the fraction who had a **distinct later ENGAGED
    session within 7 days** of that first one.

    The "engaged" filter on the return is load-bearing: without it (the original
    bug) a user who later sent a single "hi" counted as returned, and since
    engaged users generate many such incidental one-message sittings, the rate
    was wildly inflated (74% live, above every cohort in the grid). Requiring the
    return to be a genuine engaged sitting (≥ENGAGED_MIN_USER_MSGS user turns),
    strictly after the first, is the cohort definition of "came back".
    Cohort size is the sample size."""
    row = await pool.fetchrow(
        """
        WITH first_engaged AS (
            SELECT user_id, min(started_at) AS first_at
            FROM analytics.analytics_sessions
            WHERE user_turns >= $1
            GROUP BY user_id
        ),
        cohort AS (
            SELECT user_id, first_at
            FROM first_engaged
            WHERE first_at <  (now() AT TIME ZONE 'utc') - interval '7 days'
              AND first_at >= (now() AT TIME ZONE 'utc') - interval '21 days'
        ),
        returned AS (
            -- a DISTINCT later ENGAGED sitting within 7 days — not just any
            -- session (the original omission that inflated the number).
            SELECT DISTINCT c.user_id
            FROM cohort c
            JOIN analytics.analytics_sessions s
              ON s.user_id = c.user_id
             AND s.user_turns >= $1
             AND s.started_at >  c.first_at
             AND s.started_at <= c.first_at + interval '7 days'
        )
        SELECT
            (SELECT count(*) FROM returned) AS returned,
            (SELECT count(*) FROM cohort)   AS cohort_size
        """,
        config.ENGAGED_MIN_USER_MSGS,
    )
    cohort_size = row["cohort_size"] or 0
    returned = row["returned"] or 0
    return {
        "rate": (returned / cohort_size) if cohort_size else None,
        "returned": returned,
        "n": cohort_size,
    }
