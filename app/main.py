import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import config
import database
from routes.health import router as health_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {config.APP_NAME} v{config.APP_VERSION}")
    logger.info(f"Environment: {config.ENVIRONMENT}")

    # Open the read-only pool eagerly so /healthz reflects real DB reachability
    # from the first request. No background loops yet — the hourly sessionization
    # refresher arrives in Phase A. Keeping startup boring is deliberate: this
    # service must never become a dependency the chat path can trip over.
    try:
        await database.get_pool()
        logger.info("Analytics database pool initialized successfully")
    except Exception as e:
        # Don't crash on a DB blip — the service still boots and /healthz
        # reports the DB as unreachable. Isolation means analytics degrades
        # gracefully on its own, never cascading.
        logger.error(f"Failed to initialize database pool at startup: {e}")

    yield

    logger.info("Shutting down...")
    await database.close_pool()
    logger.info("Shutdown complete")


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    lifespan=lifespan,
)

app.include_router(health_router)
