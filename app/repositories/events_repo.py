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
