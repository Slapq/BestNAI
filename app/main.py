"""BestNAI FastAPI application.

Surface map:

| Prefix        | Dialect                       | Error envelope                     |
|---------------|-------------------------------|------------------------------------|
| `/native/*`   | NovelAI native                | `{message, code, request_id}`      |
| `/ai/*`, `/user/*` | YesNAI / Aaalice Launcher | `{message, code}`                  |
| `/v1/nai/*`, `/v1/images/*`, `/v1/chat/*`, `/v1/models` | YesNAI + [OI] | `{error: {message, type, code}}` |
| `/admin/*`    | administration                | `{error: {code, message, ...}}`    |
| `/health`     | health                        | plain JSON                         |
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path as _Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse as _FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles as _StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from .db import Database
from .errors import (
    ErrorCodes,
    PlatformError,
    error_payload,
    launcher_error_payload,
    native_error_payload,
    openai_error_payload,
    status_for_code,
)
from .image_store import image_store
from .site_options import get_site_options, set_site_option
from .pool import key_sync_loop, sync_pool
from .pricing_store import seed_built_in_rules
from .routes import admin, estimate, health, launcher, legacy, native, ynai
from .routes.deps import request_id_of
from .scheduler import KeyScheduler

_FRONTEND_DIR = _Path(__file__).resolve().parent.parent / "frontend"

#: Paths that must never be served by the static frontend mount.
_RESERVED_PREFIXES = (
    "/native",
    "/ai",
    "/user",
    "/v1",
    "/admin",
    "/health",
    "/ready",
    "/static",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _envelope_for(path: str) -> str:
    """Select the error envelope implied by the request path."""
    if path.startswith("/native"):
        return "native"
    if path.startswith("/ai/") or path.startswith("/user/"):
        return "launcher"
    if path.startswith("/v1"):
        return "openai"
    return "platform"


def _render_error(
    request: Request,
    *,
    code: str,
    message: str | None,
    status_code: int,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = _envelope_for(request.url.path)
    request_id = request_id_of(request)
    if envelope == "native":
        payload = native_error_payload(code, message, request_id=request_id)
    elif envelope == "launcher":
        payload = launcher_error_payload(code, message)
    elif envelope == "openai":
        payload = openai_error_payload(code, message)
    else:
        payload = error_payload(code, message or code, status_code, details)
    return JSONResponse(content=payload, status_code=status_code, headers=headers or {})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = Settings.from_env()
    app.state.settings = settings

    image_store.configure(
        max_upload_bytes=settings.max_upload_bytes,
        max_entries=settings.max_image_store_entries,
    )

    db = Database(settings.database_path)
    await db.initialize()
    await seed_built_in_rules(db)
    # Seed the free-tier rate limit options from the environment on first run.
    options = await get_site_options(db)
    if options.get("FreeGenerationRateLimitEnabled") is True and not await _has_option(
        db, "FreeGenerationRateLimitCount"
    ):
        await set_site_option(db, "FreeGenerationRateLimitEnabled", settings.free_rate_limit_enabled)
        await set_site_option(db, "FreeGenerationRateLimitCount", settings.free_rate_limit_count)
        await set_site_option(
            db, "FreeGenerationRateLimitDurationMinutes", settings.free_rate_limit_minutes
        )
    app.state.db = db

    scheduler = KeyScheduler(
        settings.api_keys,
        timeout=settings.queue_timeout_seconds,
        image_base=settings.novelai_image_base,
        api_base=settings.novelai_api_base,
    )
    app.state.scheduler = scheduler

    if settings.dynamic_pool_enabled:
        try:
            await sync_pool(db, scheduler)
        except Exception:
            # A transient failure must not prevent startup; the sync loop recovers.
            pass

    stop_event = asyncio.Event()
    app.state.sync_stop_event = stop_event
    sync_task: asyncio.Task[None] | None = None
    if settings.dynamic_pool_enabled:
        sync_task = asyncio.create_task(
            key_sync_loop(
                db,
                scheduler,
                interval_seconds=settings.key_sync_interval_seconds,
                stop_event=stop_event,
            )
        )
    app.state.sync_task = sync_task

    try:
        yield
    finally:
        stop_event.set()
        if sync_task is not None:
            sync_task.cancel()
            try:
                await sync_task
            except (asyncio.CancelledError, Exception):
                pass
        await scheduler.close()
        await db.close()


async def _has_option(db: Database, key: str) -> bool:
    row = await db.fetchone("SELECT key FROM site_options WHERE key = ?", (key,))
    return row is not None


app = FastAPI(title="BestNAI Gateway", version="2.0.0", lifespan=lifespan)


def _configure_cors() -> None:
    raw = os.getenv("BESTNAI_CORS_ORIGINS") or os.getenv("CORS_ORIGINS") or ""
    origins = [item.strip() for item in raw.replace(",", "\n").split("\n") if item.strip()]
    if not origins:
        # Local development default. Restrict through BESTNAI_CORS_ORIGINS in production.
        origins = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-YesNAI-Gems-Charged", "X-YesNAI-Estimated-Anlas", "X-Request-Id"],
    )


_configure_cors()


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    request_id_of(request)
    response = await call_next(request)
    response.headers.setdefault("X-Request-Id", request_id_of(request))
    return response


@app.exception_handler(PlatformError)
async def _platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
    return _render_error(
        request,
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
    )


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        code = str(detail["code"])
        message = str(detail.get("message") or code)
        status = int(detail.get("status_code") or exc.status_code)
    else:
        code = _code_for_status(exc.status_code)
        message = str(detail) if detail else code
        status = exc.status_code
    return _render_error(
        request,
        code=code,
        message=message,
        status_code=status,
        headers=dict(exc.headers or {}),
    )


def _code_for_status(status: int) -> str:
    from .errors import GENERIC_CODES_BY_STATUS

    return GENERIC_CODES_BY_STATUS.get(status, ErrorCodes.INTERNAL_ERROR)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _render_error(
        request,
        code="INVALID_NATIVE_REQUEST",
        message="Request validation failed",
        status_code=422,
        details={"errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return _render_error(
        request,
        code="INTERNAL_ERROR",
        message="The server encountered an internal error.",
        status_code=500,
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(health.router)
app.include_router(native.router)
app.include_router(launcher.router)
app.include_router(ynai.router)
app.include_router(estimate.router)
app.include_router(admin.router)
app.include_router(legacy.router)


# ── Frontend ──────────────────────────────────────────────────────────────────

if _FRONTEND_DIR.exists():
    @app.get("/")
    async def _root() -> _FileResponse:
        return _FileResponse(_FRONTEND_DIR / "index.html")

    @app.get("/admin/ui")
    async def _admin_ui() -> _FileResponse:
        return _FileResponse(_FRONTEND_DIR / "admin-keys.html")

    app.mount("/static", _StaticFiles(directory=str(_FRONTEND_DIR)), name="frontend")
