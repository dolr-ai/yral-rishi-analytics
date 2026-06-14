"""View 1 — cohort retention (the Comeback Curve).

Groups users by the week of their FIRST engaged session (the cohort), then for
each later week measures how many of that cohort had ANY session. The grid's
row is a cohort; the columns are weeks-since; a row that FLATTENS above zero is
the PMF tell (Andrew Chen), and week-offset 1 is the W1 return number the
headline reports. Read-only on the replica against the small analytics_sessions
table — same shape as the other repos.
"""

import config


async def cohort_retention(read_pool, weeks_back: int = 12) -> list[dict]:
    """One row per (cohort_week, week_offset) for the last `weeks_back` cohorts,
    plus the cohort size. Engagement threshold (ENGAGED_MIN_USER_MSGS) defines
    who enters a cohort and is applied at read time (hot-editable)."""
    rows = await read_pool.fetch(
        """
        WITH first_engaged AS (
            SELECT user_id, min(started_at) AS first_at
            FROM analytics.analytics_sessions
            WHERE user_turns >= $1
            GROUP BY user_id
        ),
        cohort AS (
            SELECT user_id, date_trunc('week', first_at) AS cohort_week
            FROM first_engaged
            WHERE first_at >= date_trunc(
                'week', (now() AT TIME ZONE 'utc') - make_interval(weeks => $2))
        ),
        -- every week each user had any session at all (return = any activity)
        activity AS (
            SELECT DISTINCT user_id, date_trunc('week', started_at) AS active_week
            FROM analytics.analytics_sessions
        ),
        cells AS (
            SELECT c.cohort_week,
                   (date_part('epoch', a.active_week - c.cohort_week)
                        / 604800)::int AS week_offset,
                   count(DISTINCT c.user_id) AS active_users
            FROM cohort c
            JOIN activity a
              ON a.user_id = c.user_id AND a.active_week >= c.cohort_week
            GROUP BY c.cohort_week, week_offset
        ),
        sizes AS (
            SELECT cohort_week, count(*) AS cohort_size
            FROM cohort GROUP BY cohort_week
        )
        SELECT s.cohort_week, s.cohort_size, c.week_offset, c.active_users
        FROM sizes s
        LEFT JOIN cells c ON c.cohort_week = s.cohort_week
        ORDER BY s.cohort_week DESC, c.week_offset
        """,
        config.ENGAGED_MIN_USER_MSGS,
        weeks_back,
    )
    return [dict(r) for r in rows]
