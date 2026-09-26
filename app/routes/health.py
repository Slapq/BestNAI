"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..db import Database
from ..scheduler import KeyScheduler

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Liveness plus pool and queue counters.

    The queue counters are the only queue diagnostics BestNAI exposes; a queue
    timeout itself surfaces to callers as HTTP 503 (see the API docs).
    """
    scheduler: KeyScheduler | None = getattr(request.app.state, "scheduler", None)
    db: Database | None = getattr(request.app.state, "db", None)
    settings = getattr(request.app.state, "settings", None)

    payload = {
        "status": "ok",
        "service": "bestnai",
        "queue_timeout_seconds": getattr(settings, "queue_timeout_seconds", None),
        "dynamic_pool_enabled": bool(getattr(settings, "dynamic_pool_enabled", False)),
    }
    if scheduler is not None:
        payload.update(scheduler.health())
    else:
        payload.update({"mode": "unavailable", "total_keys": 0})
    payload["database_ready"] = db is not None and db._conn is not None
    return JSONResponse(content=payload)


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: the database must be connected and the scheduler present."""
    scheduler = getattr(request.app.state, "scheduler", None)
    db: Database | None = getattr(request.app.state, "db", None)
    if scheduler is None or db is None or db._conn is None:
        return JSONResponse(content={"status": "starting"}, status_code=503)
    return JSONResponse(content={"status": "ready", **scheduler.health()})
