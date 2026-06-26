"""Product-analytics queries on analytics.raw_events (ClickHouse).

The Mixpanel/PostHog-style numbers: active users (DAU/WAU/MAU), stickiness,
event volume, top events, top screens, platform/version breakdown, and a
configurable funnel. Read-only via the analytics_reader client.

Snowplow shape: typed columns (event_id, event_name, collector_tstamp,
domain_userid, platform, …) plus the full record as JSON in `event`. Struct
event fields (se_category/se_action) and the mobile contexts live in that JSON,
read with JSONExtract*. Every count carries its sample size for honest small-N.
"""

import config

_DB = config.CLICKHOUSE_DATABASE

# Active-user identity = user_id (domain_userid is non-granular — a single value
# across the whole dataset). Fall back to the client_session context's userId
# for events fired before login.
_USER = (
    "coalesce(nullIf(JSONExtractString(event, 'user_id'), ''), "
    "JSONExtractString(event, "
    "'contexts_com_snowplowanalytics_mobile_client_session_1', 1, 'userId'))"
)

# Mobile self-describing context (1-element array in the JSON).
_APP_VERSION = (
    "JSONExtractString(event, "
    "'contexts_com_snowplowanalytics_mobile_application_1', 1, 'version')"
)

# Influencer/bot id + type — confirmed from live data (Rishi 2026-06-26):
# se_property is itself a JSON blob on the chat events; the id is nested inside.
# (Same blob also carries chat_session_id, message_type, message_length, source
# — available for future views.)
_INFLUENCER = (
    "JSONExtractString(JSONExtractString(event, 'se_property'), 'influencer_id')"
)
_INFLUENCER_TYPE = (
    "JSONExtractString(JSONExtractString(event, 'se_property'), 'influencer_type')"
)


async def _rows(client, sql: str) -> list[tuple]:
    return (await client.query(sql)).result_rows


async def active_users(client) -> dict:
    """DAU / WAU / MAU (uniqExact users) as of now."""
    row = (
        await _rows(
            client,
            f"""
        SELECT
            uniqExactIf({_USER}, collector_tstamp >= now() - INTERVAL 1 DAY)  AS dau,
            uniqExactIf({_USER}, collector_tstamp >= now() - INTERVAL 7 DAY)  AS wau,
            uniqExactIf({_USER}, collector_tstamp >= now() - INTERVAL 30 DAY) AS mau
        FROM {_DB}.raw_events
        WHERE collector_tstamp >= now() - INTERVAL 30 DAY
        """,
        )
    )[0]
    dau, wau, mau = row[0], row[1], row[2]
    stickiness = (dau / mau) if mau else None  # PostHog DAU/MAU signal
    return {"dau": dau, "wau": wau, "mau": mau, "stickiness": stickiness}


async def dau_trend(client) -> list[tuple]:
    """(date, distinct users) per day, last 30 days."""
    return await _rows(
        client,
        f"""
        SELECT toDate(collector_tstamp) AS d, uniqExact({_USER}) AS dau
        FROM {_DB}.raw_events
        WHERE collector_tstamp >= now() - INTERVAL 30 DAY
        GROUP BY d ORDER BY d
        """,
    )


async def event_volume(client) -> list[tuple]:
    """(date, event count) per day, last 30 days."""
    return await _rows(
        client,
        f"""
        SELECT toDate(collector_tstamp) AS d, count() AS events
        FROM {_DB}.raw_events
        WHERE collector_tstamp >= now() - INTERVAL 30 DAY
        GROUP BY d ORDER BY d
        """,
    )


async def top_events(client, limit: int = 20) -> list[tuple]:
    """(se_category, se_action, count) for struct events, last 7 days."""
    return await _rows(
        client,
        f"""
        SELECT JSONExtractString(event, 'se_category') AS category,
               JSONExtractString(event, 'se_action')   AS action,
               count() AS n
        FROM {_DB}.raw_events
        WHERE collector_tstamp >= now() - INTERVAL 7 DAY
          AND JSONExtractString(event, 'se_action') != ''
        GROUP BY category, action ORDER BY n DESC LIMIT {int(limit)}
        """,
    )


async def top_views(client, limit: int = 20) -> list[tuple]:
    """(view name, count) for the *_viewed se_actions, last 7 days. The
    mobile_screen context is MainActivity-only (useless), so the `_viewed`
    events are the real screen/view signal."""
    return await _rows(
        client,
        f"""
        SELECT JSONExtractString(event, 'se_action') AS view, count() AS n
        FROM {_DB}.raw_events
        WHERE collector_tstamp >= now() - INTERVAL 7 DAY
          AND endsWith(JSONExtractString(event, 'se_action'), '_viewed')
        GROUP BY view ORDER BY n DESC LIMIT {int(limit)}
        """,
    )


async def platform_breakdown(client, limit: int = 30) -> list[tuple]:
    """(platform, app version, count) last 7 days."""
    return await _rows(
        client,
        f"""
        SELECT platform, {_APP_VERSION} AS app_version, count() AS n
        FROM {_DB}.raw_events
        WHERE collector_tstamp >= now() - INTERVAL 7 DAY
        GROUP BY platform, app_version ORDER BY n DESC LIMIT {int(limit)}
        """,
    )


async def data_span_days(client) -> float:
    """Days between the oldest event and now — drives the 'still filling in'
    honesty banner (data only started flowing today)."""
    val = (
        await _rows(
            client,
            f"SELECT dateDiff('hour', min(collector_tstamp), now()) / 24.0 "
            f"FROM {_DB}.raw_events",
        )
    )[0][0]
    return float(val or 0)


async def influencer_engagement(client, limit: int = 30) -> list[tuple]:
    """Per influencer/bot: type, chats started, messages sent, distinct users —
    the 'which bots create love' view, last 30 days. Empty list = the id field
    (_INFLUENCER) found nothing → confirm the field."""
    return await _rows(
        client,
        f"""
        SELECT influencer, any(itype) AS influencer_type,
               countIf(action = 'chat_session_started') AS chats,
               countIf(action = 'user_message_sent')    AS messages,
               uniqExact(u) AS users
        FROM (
            SELECT {_INFLUENCER} AS influencer, {_INFLUENCER_TYPE} AS itype,
                   {_USER} AS u, JSONExtractString(event, 'se_action') AS action
            FROM {_DB}.raw_events
            WHERE collector_tstamp >= now() - INTERVAL 30 DAY
        )
        WHERE influencer != ''
          AND action IN ('chat_session_started', 'user_message_sent')
        GROUP BY influencer ORDER BY messages DESC, chats DESC LIMIT {int(limit)}
        """,
    )


async def message_depth(client) -> list[tuple]:
    """Power-user curve: how many users sent 1 / 2-5 / 6-20 / 21+ messages
    (user_message_sent), all time. Are a few users deeply hooked?"""
    rows = await _rows(
        client,
        f"""
        SELECT multiIf(c = 1, '1', c <= 5, '2-5', c <= 20, '6-20', '21+') AS bucket,
               count() AS users
        FROM (
            SELECT {_USER} AS u,
                   countIf(JSONExtractString(event, 'se_action') = 'user_message_sent') AS c
            FROM {_DB}.raw_events GROUP BY u HAVING c > 0
        )
        GROUP BY bucket
        """,
    )
    order = {"1": 0, "2-5": 1, "6-20": 2, "21+": 3}
    return sorted(rows, key=lambda r: order.get(r[0], 9))


async def returning_user_rate(client) -> dict:
    """Share of users seen on more than one distinct day (a simple, robust
    'they came back' signal that works even with a few days of data)."""
    row = (
        await _rows(
            client,
            f"""
        SELECT countIf(days > 1) AS returning, count() AS total FROM (
            SELECT {_USER} AS u, uniqExact(toDate(collector_tstamp)) AS days
            FROM {_DB}.raw_events GROUP BY u
        )
        """,
        )
    )[0]
    returning, total = row[0], row[1]
    return {"rate": (returning / total) if total else None, "n": total}


async def event_retention(client, weeks_back: int = 8) -> list[tuple]:
    """Weekly new-user cohort retention (PostHog-style) on raw_events: cohort =
    week of a user's FIRST event; cell = users active that week-offset. Returns
    (cohort_week, cohort_size, week_offset, active_users)."""
    return await _rows(
        client,
        f"""
        WITH first_seen AS (
            SELECT {_USER} AS u, toMonday(min(toDate(collector_tstamp))) AS cohort_week
            FROM {_DB}.raw_events GROUP BY u
            HAVING cohort_week >= toMonday(now()) - INTERVAL {int(weeks_back)} WEEK
        ),
        activity AS (
            SELECT DISTINCT {_USER} AS u, toMonday(toDate(collector_tstamp)) AS active_week
            FROM {_DB}.raw_events
        ),
        cells AS (
            SELECT f.cohort_week AS cohort_week,
                   dateDiff('week', f.cohort_week, a.active_week) AS week_offset,
                   uniqExact(f.u) AS active_users
            -- CH 24.3 allows ONLY equality in JOIN ON; the >= goes in WHERE.
            -- (It's always true anyway — cohort_week is the user's first week.)
            FROM first_seen f
            JOIN activity a ON a.u = f.u
            WHERE a.active_week >= f.cohort_week
            GROUP BY cohort_week, week_offset
        ),
        sizes AS (
            SELECT cohort_week, uniqExact(u) AS cohort_size
            FROM first_seen GROUP BY cohort_week
        )
        SELECT s.cohort_week, s.cohort_size, c.week_offset, c.active_users
        FROM sizes s LEFT JOIN cells c ON c.cohort_week = s.cohort_week
        ORDER BY s.cohort_week DESC, c.week_offset
        """,
    )


async def wow(client) -> dict:
    """Week-over-week: active users + events, this 7d vs the prior 7d, with %
    change. The honest 'are we growing' read once a couple of weeks exist."""
    row = (
        await _rows(
            client,
            f"""
        SELECT
            uniqExactIf(u, ts >= now() - INTERVAL 7 DAY)  AS u_this,
            uniqExactIf(u, ts <  now() - INTERVAL 7 DAY)  AS u_last,
            countIf(ts >= now() - INTERVAL 7 DAY)         AS e_this,
            countIf(ts <  now() - INTERVAL 7 DAY)         AS e_last
        FROM (
            SELECT {_USER} AS u, collector_tstamp AS ts
            FROM {_DB}.raw_events WHERE collector_tstamp >= now() - INTERVAL 14 DAY
        )
        """,
        )
    )[0]
    u_this, u_last, e_this, e_last = row

    def delta(this, last):
        return ((this - last) / last) if last else None

    return {
        "users_this": u_this,
        "users_last": u_last,
        "users_delta": delta(u_this, u_last),
        "events_this": e_this,
        "events_last": e_last,
        "events_delta": delta(e_this, e_last),
    }


async def events_by_action(client, limit: int = 20) -> list[tuple]:
    """Breakdown by event-type (se_action) across ALL events, last 7 days."""
    return await _rows(
        client,
        f"""
        SELECT JSONExtractString(event, 'se_action') AS action, count() AS n
        FROM {_DB}.raw_events
        WHERE collector_tstamp >= now() - INTERVAL 7 DAY
          AND JSONExtractString(event, 'se_action') != ''
        GROUP BY action ORDER BY n DESC LIMIT {int(limit)}
        """,
    )


async def recent_activity(client) -> dict:
    """Last-15-min + last-hour event counts and 15-min active users — for the
    auto-refreshing 'is it alive right now' tile."""
    row = (
        await _rows(
            client,
            f"""
        SELECT countIf(ts >= now() - INTERVAL 15 MINUTE)         AS e15,
               countIf(ts >= now() - INTERVAL 60 MINUTE)         AS e60,
               uniqExactIf(u, ts >= now() - INTERVAL 15 MINUTE)  AS u15
        FROM (
            SELECT {_USER} AS u, collector_tstamp AS ts
            FROM {_DB}.raw_events WHERE collector_tstamp >= now() - INTERVAL 60 MINUTE
        )
        """,
        )
    )[0]
    return {"events_15m": row[0], "events_60m": row[1], "users_15m": row[2]}


# ── Funnel (configurable) ────────────────────────────────────────────────
# The core product funnel (Rishi, 2026-06-26): land → browse bots → start a
# chat → send the first message. Conversion + per-step drop-off is the headline.
FUNNEL_STEPS: list[str] = [
    "home_page_viewed",
    "influencer_cards_viewed",
    "chat_session_started",
    "user_message_sent",
]


async def funnel(client, steps: list[str], window_days: int = 7) -> list[dict]:
    """Ordered conversion through `steps` (se_action values) within a per-user
    window, via ClickHouse windowFunnel. Returns one row per step with the
    users who reached it + conversion vs the first step. Empty steps → []."""
    if not steps:
        return []
    conds = ", ".join(f"se_action = '{s}'" for s in steps)  # steps are operator-set
    window = window_days * 86400
    rows = await _rows(
        client,
        f"""
        SELECT level, count() AS users FROM (
            SELECT {_USER} AS u,
                   windowFunnel({window})(toDateTime(collector_tstamp), {conds}) AS level
            FROM (
                SELECT {_USER}, collector_tstamp,
                       JSONExtractString(event, 'se_action') AS se_action,
                       domain_userid, event
                FROM {_DB}.raw_events
                WHERE collector_tstamp >= now() - INTERVAL {int(window_days)} DAY
            )
            GROUP BY u
        )
        GROUP BY level ORDER BY level
        """,
    )
    # windowFunnel returns the max level reached; "reached step k" = level >= k.
    by_level = {int(level): users for level, users in rows}
    reached = []
    cumulative = sum(by_level.values())
    base = None
    for i, step in enumerate(steps, start=1):
        cumulative -= by_level.get(i - 1, 0)
        if base is None:
            base = cumulative
        reached.append(
            {
                "step": step,
                "users": cumulative,
                "conversion": (cumulative / base) if base else None,
            }
        )
    return reached
