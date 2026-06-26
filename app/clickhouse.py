"""Read-only ClickHouse access for the dashboard service.

Mirrors database.py (the Postgres pool): a lazily-created async client, DSN
read file-first from a Swarm secret. This is the analytics_reader (read-only)
user — separate from the consumer's write DSN — so the dashboard can never
write to ClickHouse. The dashboards only touch the small `analytics.raw_events`
aggregations, so a single shared client is plenty.
"""

import logging

import clickhouse_connect

import config

logger = logging.getLogger(__name__)

_client = None


def _read_dsn() -> str:
    # Swarm secret mounts as a file (more secure than env); env is the dev
    # fallback. Read-only analytics_reader user.
    import os

    secret_file = "/run/secrets/analytics_clickhouse_reader_dsn"
    if os.path.exists(secret_file):
        with open(secret_file) as f:
            return f.read().strip()
    if not config.CLICKHOUSE_READER_DSN:
        raise RuntimeError("CLICKHOUSE_READER_DSN is not set (no secret file, no env)")
    return config.CLICKHOUSE_READER_DSN


async def get_client():
    global _client
    if _client is None:
        _client = await clickhouse_connect.get_async_client(dsn=_read_dsn())
    return _client


async def check_health() -> bool:
    try:
        client = await get_client()
        await client.query("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"ClickHouse health check failed: {e}")
        return False


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
