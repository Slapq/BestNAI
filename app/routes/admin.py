"""Administration surface: key pool, pricing rules, and site options."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ..auth import require_admin
from ..errors import PlatformError, error_payload
from ..generation import normalize_native_payload
from ..key_store import (
    create_key,
    delete_keys,
    get_key_row,
    list_key_rows,
    row_to_public,
    set_key_enabled,
    update_key_note,
)
from ..site_options import (
    DEFAULT_SITE_OPTIONS,
    SITE_OPTION_ANLAS_RATIO,
    SITE_OPTION_PRICING_STRATEGY,
    get_site_options,
    set_site_option,
)
from ..pool import refresh_account_snapshot, sync_pool
from ..pricing_store import (
    VALID_PRICING_STRATEGIES,
    create_rule,
    delete_rule,
    get_built_in_free_policy_rule,
    list_rule_rows,
    resolve_generation_pricing,
    set_rule_enabled,
    to_public_rule,
    update_anlas_fallback_policy,
    update_built_in_free_policy,
    update_rule,
)
from .deps import get_db, get_scheduler

router = APIRouter(prefix="/admin", tags=["admin"])


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(content=error_payload(code, message, status), status_code=status)


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise PlatformError("INVALID_NATIVE_REQUEST", "Request body must be valid JSON", 400) from exc
    if not isinstance(body, dict):
        raise PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
    return body


# ── Status ────────────────────────────────────────────────────────────────────


@router.get("/status")
async def admin_status(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    from ..image_store import image_store

    rows = await list_key_rows(db, limit=500)
    return JSONResponse(
        content={
            **scheduler.health(),
            "stored_keys": len(rows),
            "image_store_entries": len(image_store._store),
        }
    )


# ── Keys ──────────────────────────────────────────────────────────────────────


@router.get("/keys")
async def admin_list_keys(
    request: Request,
    enabled: bool | None = Query(default=None),
    keyword: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    _: str = Depends(require_admin),
) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    rows = await list_key_rows(db, enabled=enabled, keyword=keyword, limit=limit)
    return JSONResponse(
        content={
            **scheduler.health(),
            "keys": [row_to_public(row) for row in rows],
        }
    )


@router.post("/keys")
async def admin_add_key(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    body = await _body(request)

    api_key = str(body.get("api_key") or "").strip()
    if not api_key:
        return _error("INVALID_NATIVE_REQUEST", "api_key is required", 400)
    if not api_key.startswith("pst-"):
        return _error("INVALID_NATIVE_REQUEST", "Invalid NovelAI API key format", 400)

    try:
        row = await create_key(db, api_key=api_key, note=str(body.get("note") or ""))
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)

    await sync_pool(db, scheduler)
    # Best-effort: record the account information for display. A failure here
    # does not prevent the key from serving traffic.
    refreshed = await refresh_account_snapshot(db, scheduler, row["key_hash"])
    return JSONResponse(
        content={
            "key": row_to_public(refreshed or row),
            "account_checked": bool((refreshed or row).get("account_checked_at")),
        },
        status_code=201,
    )


@router.patch("/keys/{key_id}")
async def admin_update_key(
    key_id: str,
    request: Request,
    _: str = Depends(require_admin),
) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    body = await _body(request)

    if "note" not in body:
        return _error("INVALID_NATIVE_REQUEST", "Nothing to update", 400)

    try:
        updated = await update_key_note(db, key_id, str(body.get("note") or ""))
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)

    await sync_pool(db, scheduler)
    return JSONResponse(content={"key": row_to_public(updated)})


@router.post("/keys/{key_id}/enabled")
async def admin_set_key_enabled(
    key_id: str,
    request: Request,
    enabled: bool = Query(...),
    _: str = Depends(require_admin),
) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    try:
        row = await set_key_enabled(db, key_id, enabled)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    await sync_pool(db, scheduler)
    return JSONResponse(content={"key": row_to_public(row)})


@router.delete("/keys/{key_id}")
async def admin_delete_key(key_id: str, request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    row = await get_key_row(db, key_id)
    if row is None:
        return _error("NOT_FOUND", "Key not found", 404)
    slot = scheduler.get_slot(row["key_hash"])
    if slot is not None and slot.busy:
        return _error("INVALID_NATIVE_REQUEST", "Cannot delete a busy key", 409)
    deleted = await delete_keys(db, [key_id])
    await sync_pool(db, scheduler)
    return JSONResponse(content={"deleted": deleted})


@router.post("/keys/batch-delete")
async def admin_batch_delete(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    body = await _body(request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        return _error("INVALID_NATIVE_REQUEST", "ids must be a non-empty list", 400)
    if len(ids) > 200:
        return _error("INVALID_NATIVE_REQUEST", "at most 200 ids may be deleted at once", 400)

    busy: list[str] = []
    for item in ids:
        row = await get_key_row(db, str(item))
        if row is None:
            continue
        slot = scheduler.get_slot(row["key_hash"])
        if slot is not None and slot.busy:
            busy.append(str(item))
    if busy:
        return _error("INVALID_NATIVE_REQUEST", "Cannot delete busy keys: " + ", ".join(busy), 409)

    deleted = await delete_keys(db, [str(item) for item in ids])
    await sync_pool(db, scheduler)
    return JSONResponse(content={"deleted": deleted})


@router.post("/keys/{key_id}/refresh-account")
async def admin_refresh_account(key_id: str, request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    row = await get_key_row(db, key_id)
    if row is None:
        return _error("NOT_FOUND", "Key not found", 404)
    updated = await refresh_account_snapshot(db, scheduler, row["key_hash"])
    return JSONResponse(
        content={
            "key": row_to_public(updated or row),
            "account_checked": bool((updated or row).get("account_checked_at")),
        }
    )


@router.post("/keys/refresh-accounts")
async def admin_refresh_accounts(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    rows = await list_key_rows(db, enabled=True, limit=500)
    checked = 0
    failed = 0
    for row in rows:
        updated = await refresh_account_snapshot(db, scheduler, row["key_hash"])
        if updated is not None and updated.get("account_checked_at") and not updated.get("account_error"):
            checked += 1
        else:
            failed += 1
    return JSONResponse(content={"requested": len(rows), "checked": checked, "failed": failed})


# ── Pricing rules ─────────────────────────────────────────────────────────────


@router.get("/pricing/rules")
async def admin_list_rules(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    rows = await list_rule_rows(db)
    return JSONResponse(content={"rules": [to_public_rule(row) for row in rows]})


@router.post("/pricing/rules")
async def admin_create_rule(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    body = await _body(request)
    try:
        rule = await create_rule(db, body)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    return JSONResponse(content={"rule": rule}, status_code=201)


@router.put("/pricing/rules/{rule_id}")
async def admin_update_rule(rule_id: int, request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    body = await _body(request)
    try:
        rule = await update_rule(db, rule_id, body)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    return JSONResponse(content={"rule": rule})


@router.post("/pricing/rules/{rule_id}/enabled")
async def admin_set_rule_enabled(
    rule_id: int,
    request: Request,
    enabled: bool = Query(...),
    _: str = Depends(require_admin),
) -> JSONResponse:
    db = get_db(request)
    try:
        rule = await set_rule_enabled(db, rule_id, enabled)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    return JSONResponse(content={"rule": rule})


@router.delete("/pricing/rules/{rule_id}")
async def admin_delete_rule(rule_id: int, request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    try:
        await delete_rule(db, rule_id)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    return JSONResponse(content={"deleted": True})


@router.get("/pricing/built-in-policies")
async def admin_built_in_policies(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    options = await get_site_options(db)
    free_rule = await get_built_in_free_policy_rule(db)
    return JSONResponse(
        content={
            "free_standard": (
                {
                    "id": free_rule.id,
                    "enabled": free_rule.enabled,
                    "allowed_sizes": free_rule.allowed_sizes,
                    "max_steps": free_rule.max_steps,
                    "max_images": free_rule.max_images,
                    "base_gems": free_rule.base_gems,
                    "txt2img_only": True,
                    "advanced_features_allowed": False,
                }
                if free_rule is not None
                else None
            ),
            "anlas_fallback": {
                "strategy": options.get(SITE_OPTION_PRICING_STRATEGY),
                "anlas_to_gems_ratio": options.get(SITE_OPTION_ANLAS_RATIO),
                "min_pixels": 64 * 64,
                "max_pixels": 3_145_728,
                "min_steps": 1,
                "max_steps": 50,
                "min_images": 1,
                "max_images": 8,
                "advanced_features_allowed": True,
            },
        }
    )


@router.put("/pricing/built-in-policies/free-standard")
async def admin_update_free_standard(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    body = await _body(request)
    try:
        rule = await update_built_in_free_policy(db, body)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    return JSONResponse(content={"rule": rule})


@router.put("/pricing/built-in-policies/anlas-fallback")
async def admin_update_anlas_fallback(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    body = await _body(request)
    try:
        rule = await update_anlas_fallback_policy(db, body)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    return JSONResponse(content={"rule": rule})


@router.post("/pricing/rules/test-quote")
async def admin_test_quote(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    """Price an arbitrary payload without creating a job."""
    db = get_db(request)
    body = await _body(request)
    try:
        native_payload = normalize_native_payload(body)
        rule, breakdown, job_fields = await resolve_generation_pricing(db, native_payload)
    except PlatformError as exc:
        return _error(exc.code, exc.message, exc.status_code)
    return JSONResponse(
        content={
            "request": job_fields,
            "matched_rule": {
                "id": rule.id,
                "model": rule.model,
                "action": rule.action,
                "size_bucket": rule.size_bucket,
                "priority": rule.priority,
                "enabled": rule.enabled,
                "base_gems": rule.base_gems,
                "built_in": rule.built_in,
                "pricing_type": rule.pricing_type,
            },
            "breakdown": breakdown,
        }
    )


# ── Site options ──────────────────────────────────────────────────────────────


@router.get("/options")
async def admin_get_options(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    options = await get_site_options(db)
    return JSONResponse(content={"options": options, "defaults": DEFAULT_SITE_OPTIONS})


@router.put("/options")
async def admin_put_options(request: Request, _: str = Depends(require_admin)) -> JSONResponse:
    db = get_db(request)
    body = await _body(request)
    unknown = [key for key in body if key not in DEFAULT_SITE_OPTIONS]
    if unknown:
        return _error(
            "INVALID_NATIVE_REQUEST",
            "Unknown option keys: " + ", ".join(sorted(unknown)),
            422,
        )
    for key, value in body.items():
        await set_site_option(db, key, value)
    return await admin_get_options(request, _)
