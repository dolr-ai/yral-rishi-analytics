"""ClickHouse sink for the events consumer — connect, ensure schema, batch insert.

Connects as the analytics_consumer WRITE user (DSN from a Swarm secret). The
analytics.raw_events table is a ReplacingMergeTree: at-least-once delivery means
the same event_id can arrive twice (bridge restart / re-delivery), and CH
collapses identical (collector_tstamp, event_name, event_id) rows on merge —
sink-side idempotency, no exactly-once illusion. Single-node v1: plain
ReplacingMergeTree, no Keeper. Inserts are always batched (CH hates row-by-row).
"""

import logging

import clickhouse_connect

import config

logger = logging.getLogger(__name__)

# Column order for inserts — must match raw_events below and snowplow.to_row().
_COLUMNS = [
    "event_id",
    "event_name",
    "collector_tstamp",
    "derived_tstamp",
    "app_id",
    "platform",
    "domain_userid",
    "domain_sessionid",
    "event",
]

# Typed columns for the fields we always want to filter/group on; the full
# parsed record lives in `event` as JSON text (CH 24.3 has no stable JSON type,
# so String + JSONExtract* at query time). ReplacingMergeTree dedups on the
# ORDER BY key, which includes event_id.
_DDL = """
CREATE TABLE IF NOT EXISTS {db}.raw_events
(
    event_id          String,
    event_name        LowCardinality(String),
    collector_tstamp  DateTime64(3, 'UTC'),
    derived_tstamp    Nullable(DateTime64(3, 'UTC')),
    app_id            LowCardinality(String),
    platform          LowCardinality(String),
    domain_userid     String,
    domain_sessionid  String,
    event             String
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(collector_tstamp)
ORDER BY (collector_tstamp, event_name, event_id)
TTL toDateTime(collector_tstamp) + INTERVAL 90 DAY
"""


class ClickHouseSink:
    def __init__(self):
        self._client = None

    async def _connect(self):
        if self._client is None:
            self._client = await clickhouse_connect.get_async_client(
                dsn=config.CLICKHOUSE_DSN
            )
        return self._client

    async def ensure_table(self) -> None:
        client = await self._connect()
        await client.command(_DDL.format(db=config.CLICKHOUSE_DATABASE))

    async def insert(self, rows: list[tuple]) -> None:
        if not rows:
            return
        client = await self._connect()
        await client.insert(
            f"{config.CLICKHOUSE_DATABASE}.raw_events",
            rows,
            column_names=_COLUMNS,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
