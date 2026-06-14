import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _read_dsn() -> str:
    # Docker secrets are mounted as files — more secure than env vars, and
    # the same pattern the chat service uses (SYMMETRY). The DSN points at a
    # Patroni READ REPLICA; never the leader.
    secret_file = "/run/secrets/analytics_db_dsn"
    if os.path.exists(secret_file):
        with open(secret_file) as f:
            return f.read().strip()
    import config

    if not config.ANALYTICS_DB_DSN:
        raise RuntimeError("ANALYTICS_DB_DSN is not set (no secret file, no env)")
    return config.ANALYTICS_DB_DSN


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    dsn = _read_dsn()
    logger.info("Creating analytics database connection pool (read-only replica)...")

    # server_settings forces every connection in this pool read-only as a
    # belt-and-braces guard. The analytics_ro role already enforces read-only
    # at the DB level (ALTER ROLE ... SET default_transaction_read_only = on),
    # but setting it here too means a misconfigured role can never let a write
    # slip through from this service. A small pool — this is a low-traffic
    # internal tool that must never crowd out the chat path on the replica.
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=4,
        command_timeout=30,
        server_settings={"default_transaction_read_only": "on"},
    )

    logger.info("Analytics database connection pool created successfully")
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Analytics database connection pool closed")


async def check_db_health() -> bool:
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Analytics database health check failed: {e}")
        return False
