from fastapi import APIRouter, HTTPException

import config
import database

router = APIRouter(tags=["Health"])


@router.get("/")
async def root():
    return {
        "service": config.APP_NAME,
        "version": config.APP_VERSION,
        "status": "running",
    }


@router.get("/healthz")
async def healthz():
    # The cluster watches this. Returns ok only when the read-only replica
    # pool is reachable, so a silently-broken DB connection surfaces instead
    # of the service appearing healthy while it can read nothing (the
    # "zombie service" failure mode this project was bitten by before).
    if not await database.check_db_health():
        raise HTTPException(
            status_code=503,
            detail={"status": "error", "database": "unreachable"},
        )
    return {"status": "ok", "database": "reachable"}
