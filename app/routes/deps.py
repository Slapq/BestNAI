"""Shared FastAPI dependencies for the BestNAI routers."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request

from ..config import Settings
from ..db import Database
from ..errors import PlatformError
from ..image_store import image_store
from ..scheduler import KeyScheduler


def get_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise PlatformError("INTERNAL_ERROR", "Settings not ready", 500)
    return settings


def get_db(request: Request) -> Database:
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise PlatformError("INTERNAL_ERROR", "Database not ready", 500)
    return db


def get_scheduler(request: Request) -> KeyScheduler:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise PlatformError("INTERNAL_ERROR", "Scheduler not ready", 500)
    return scheduler


def get_image_store():
    return image_store


def request_language(request: Request) -> str:
    """Resolve the response language from the query, then the Accept-Language header."""
    explicit = request.query_params.get("lang")
    if explicit:
        return "zh" if explicit.lower().startswith("zh") else "en"
    header = request.headers.get("accept-language", "")
    if header.lower().startswith("zh"):
        return "zh"
    settings = getattr(request.app.state, "settings", None)
    return getattr(settings, "default_language", "en") if settings else "en"


def request_id_of(request: Request) -> str:
    """Return the request correlation id, generating one when absent."""
    existing = getattr(request.state, "request_id", None)
    if existing:
        return str(existing)
    generated = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = generated
    return generated


def idempotency_key_of(request: Request) -> str | None:
    return request.headers.get("idempotency-key")


def resolve_upload_refs(payload: Any) -> Any:
    """Replace `upload_id:<id>` references with stored image data."""
    try:
        return image_store.resolve_in_object(payload)
    except KeyError as exc:
        raise PlatformError("INVALID_NATIVE_REQUEST", str(exc), 400) from exc
