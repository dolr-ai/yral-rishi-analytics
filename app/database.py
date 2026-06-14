import os
import asyncpg
import logging

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_write_pool: asyncpg.Pool | None = None


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


def _read_dsn_rw() -> str:
    # The analytics_rw DSN points at the Patroni LEADER. This is the service's
    # ONLY leader contact — used solely by the hourly refresh job to write the
    # small summary into the analytics schema (Option B). Secret-file first.
    secret_file = "/run/secrets/analytics_db_dsn_rw"
    if os.path.exists(secret_file):
        with open(secret_file) as f:
            return f.read().strip()
    import config

    if not config.ANALYTICS_DB_DSN_RW:
        raise RuntimeError("ANALYTICS_DB_DSN_RW is not set (no secret file, no env)")
    return config.ANALYTICS_DB_DSN_RW


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


async def get_write_pool() -> asyncpg.Pool:
    # The leader (analytics_rw) pool — opened lazily and ONLY when the hourly
    # refresh job needs it. Deliberately NOT read-only (it writes the summary)
    # and tiny: this is one small hourly transaction, never a read path. Its
    # only target is the analytics schema; the rw role has no `public` access.
    global _write_pool
    if _write_pool is not None:
        return _write_pool

    dsn = _read_dsn_rw()
    logger.info("Creating analytics WRITE pool (leader; analytics schema only)...")
    _write_pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=2,
        command_timeout=90,
    )
    logger.info("Analytics write pool created successfully")
    return _write_pool


async def close_pool():
    global _pool, _write_pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Analytics database connection pool closed")
    if _write_pool is not None:
        await _write_pool.close()
        _write_pool = None
        logger.info("Analytics write pool closed")


async def check_db_health() -> bool:
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        return True
    except Exception as e:
        logger.error(f"Analytics database health check failed: {e}")
        return False
