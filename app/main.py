import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
import database
from routes.headline import router as headline_router
from routes.health import router as health_router
from services import sessions_refresh

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _sessions_refresher():
    """Refresh analytics.analytics_sessions hourly (mirrors the chat service's
    _trending_stats_refresher). Heavy read on the replica, small write to the
    leader. Never crashes the service — a failed refresh just logs and waits
    for the next tick (isolation: analytics degrades on its own)."""
    try:
        await sessions_refresh.refresh_sessions()
    except Exception:
        logger.exception("sessions refresh: initial run failed")

    while True:
        await asyncio.sleep(config.SESSIONS_REFRESH_INTERVAL_SEC)
        try:
            await sessions_refresh.refresh_sessions()
        except Exception:
            logger.exception("sessions refresh: run failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {config.APP_NAME} v{config.APP_VERSION}")
    logger.info(f"Environment: {config.ENVIRONMENT}")

    # Open the read-only pool eagerly so /healthz reflects real DB reachability
    # from the first request. Keeping startup boring is deliberate: this service
    # must never become a dependency the chat path can trip over.
    try:
        await database.get_pool()
        logger.info("Analytics database pool initialized successfully")
    except Exception as e:
        # Don't crash on a DB blip — the service still boots and /healthz
        # reports the DB as unreachable. Isolation means analytics degrades
        # gracefully on its own, never cascading.
        logger.error(f"Failed to initialize database pool at startup: {e}")

    # The hourly refresher is the ONLY thing that touches the leader, so it
    # stays dormant until the analytics_rw DSN exists (post DB-setup). No rw
    # DSN → no leader contact at all, by construction.
    refresher_task = None
    if config.ANALYTICS_DB_DSN_RW:
        refresher_task = asyncio.create_task(_sessions_refresher())
        logger.info("Sessions refresher started (hourly)")
    else:
        logger.info("ANALYTICS_DB_DSN_RW unset — sessions refresher dormant")

    yield

    logger.info("Shutting down...")
    if refresher_task is not None:
        refresher_task.cancel()
        try:
            await refresher_task
        except asyncio.CancelledError:
            pass
    await database.close_pool()
    logger.info("Shutdown complete")


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(headline_router)
