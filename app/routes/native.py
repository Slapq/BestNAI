"""NovelAI-native API surface (`/native/*`).

This mirrors the subset of the official NovelAI image API that desktop clients
such as Aaalice NAI Launcher require.  Payloads are native NovelAI structures:
low-level requests are validated and forwarded verbatim, high-level convenience
payloads go through the SDK converter.

Errors use the native envelope `{"message", "code", "request_id"}`.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..auth import require_client
from ..client import NovelAIAPIError
from ..errors import PlatformError, native_error_payload
from ..execution import (
    open_raw_stream,
    prepare_generation,
    run_generation,
    run_tool,
)
from ..generation import normalize_native_payload
from ..pool import pool_anlas
from .deps import (
    get_db,
    get_scheduler,
    get_settings,
    idempotency_key_of,
    request_id_of,
    request_language,
    resolve_upload_refs,
)
from .responses import GEMS_CHARGED_HEADER, image_response, normalize_image_format

router = APIRouter(prefix="/native", tags=["native"])

#: Raw upstream passthrough endpoints reachable through `/native/ai/{operation}`.
ALLOWED_NATIVE_OPERATIONS: dict[str, tuple[str, str]] = {
    "generate-image": ("image", "/ai/generate-image"),
    "encode-vibe": ("image", "/ai/encode-vibe"),
    "upscale": ("main", "/ai/upscale"),
    "augment-image": ("image", "/ai/augment-image"),
    "annotate-image": ("image", "/ai/annotate-image"),
}


def native_error(exc: PlatformError, request: Request) -> JSONResponse:
    return JSONResponse(
        content=native_error_payload(
            exc.code,
            exc.message,
            request_id=request_id_of(request),
        ),
        status_code=exc.status_code,
    )


async def _native_payload(request: Request) -> dict[str, Any]:
    """Read a JSON or multipart native payload."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        payload: dict[str, Any] = {}
        for key, value in form.items():
            if isinstance(value, str):
                try:
                    import json as _json

                    payload[key] = _json.loads(value)
                except (TypeError, ValueError):
                    payload[key] = value
        return payload
    body = await request.json()
    if not isinstance(body, dict):
        raise PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
    return body


def _pool_subscription(scheduler, *, include_extensions: bool) -> dict[str, Any]:
    """A Launcher-compatible subscription projection.

    BestNAI has no per-user wallet, so the reported Anlas is the pool's last-known
    upstream Anlas. Key names and the `perks` shape match YesNAI exactly;
    `include_extensions` adds the launcher-only `currency` field.
    """
    payload: dict[str, Any] = {
        "tier": 3,
        "is_opus": True,
        "active": True,
        "expiresAt": None,
        "trainingStepsLeft": {
            "fixedTrainingStepsLeft": 0,
            "purchasedTrainingSteps": pool_anlas(scheduler),
        },
        "perks": {
            "maxPriorityActions": 0,
            "startPriority": 0,
            "moduleTrainingSteps": 0,
            "unlimitedMaxPriority": False,
            "voiceGeneration": False,
            "imageGeneration": True,
            "unlimitedImageGeneration": False,
            "unlimitedImageGenerationLimits": [],
            "contextTokens": 0,
        },
        "accountType": 0,
        "isGracePeriod": False,
    }
    if include_extensions:
        payload["currency"] = "gems"
    return payload


@router.get("/user/subscription")
async def native_subscription(request: Request, _: str = Depends(require_client)) -> JSONResponse:
    scheduler = get_scheduler(request)
    return JSONResponse(content=_pool_subscription(scheduler, include_extensions=False))


@router.post("/ai/generate-image")
async def native_generate_image(
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    settings = get_settings(request)
    language = request_language(request)

    payload = await _native_payload(request)
    resolved = resolve_upload_refs(payload)
    native_payload = normalize_native_payload(resolved)
    fmt = normalize_image_format(request.query_params.get("image_format"), "png")

    try:
        prepared = await prepare_generation(
            db,
            entrypoint="native",
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
    except PlatformError as exc:
        return native_error(exc, request)

    try:
        images = await run_generation(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return native_error(exc, request)

    return image_response(
        images,
        fmt=fmt,
        response_format="zip",
        metadata={
            "job_id": prepared.job_id,
            "request_id": prepared.request_id,
            "model": prepared.job_fields["model"],
            "cost_gems": prepared.cost_gems,
            "estimated_anlas": prepared.estimated_anlas,
        },
        headers={GEMS_CHARGED_HEADER: str(prepared.cost_gems)},
    )


@router.post("/ai/generate-image-stream")
async def native_generate_image_stream(
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    payload = await _native_payload(request)
    resolved = resolve_upload_refs(payload)
    native_payload = normalize_native_payload(resolved)

    try:
        prepared = await prepare_generation(
            db,
            entrypoint="native-stream",
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
        upstream, lease = await open_raw_stream(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return native_error(exc, request)

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.response.aiter_raw():
                yield chunk
            await succeed_job(db, prepared, 1)
        except BaseException as exc:
            await fail_job(db, prepared, str(exc))
            raise
        finally:
            await upstream.close()
            await lease.release()

    media_type = upstream.response.headers.get("content-type", "application/x-msgpack").split(";", 1)[0]
    return StreamingResponse(
        body(),
        status_code=upstream.response.status_code,
        media_type=media_type,
        headers={GEMS_CHARGED_HEADER: str(prepared.cost_gems)},
    )


@router.post("/ai/{operation}")
async def native_operation(
    operation: str,
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    """Raw passthrough for native tool operations (upscale, augment, annotate...)."""
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    target = ALLOWED_NATIVE_OPERATIONS.get(operation)
    if target is None:
        return native_error(PlatformError("NOT_FOUND", "unsupported native operation", 404), request)
    if operation == "generate-image":
        # Generation is handled by the dedicated endpoint so pricing applies.
        return native_error(
            PlatformError("NOT_FOUND", "use /native/ai/generate-image", 404),
            request,
        )

    api_host, path = target
    payload = await _native_payload(request)
    resolved = resolve_upload_refs(payload)

    async def call(lease):
        return await lease.client.raw_request(
            path=path,
            payload=resolved,
            api_host=api_host,
            accept=request.headers.get("accept"),
            timeout=300.0 if request.headers.get("x-yesnai-native-passthrough") == "1" else None,
        )

    try:
        content, content_type = await run_tool(
            db,
            scheduler,
            operation=operation,
            call=call,
            language=language,
        )
    except PlatformError as exc:
        return native_error(exc, request)
    return Response(content=content, media_type=content_type.split(";", 1)[0])


@router.post("/ai/encode-vibe")
async def native_encode_vibe(
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    payload = await _native_payload(request)
    resolved = resolve_upload_refs(payload)
    image = str(resolved.get("image") or "")
    if not image:
        return native_error(PlatformError("INVALID_NATIVE_REQUEST", "image is required", 400), request)

    async def call(lease):
        return await lease.client.encode_vibe(
            image_b64=image,
            information_extracted=float(resolved.get("information_extracted", 1.0)),
            model=str(resolved.get("model") or ""),
        )

    try:
        encoded = await run_tool(
            db,
            scheduler,
            operation="encode-vibe",
            call=call,
            language=language,
        )
    except PlatformError as exc:
        return native_error(exc, request)

    import base64 as _base64

    try:
        return Response(
            content=_base64.b64decode(encoded),
            media_type="application/octet-stream",
        )
    except (ValueError, TypeError):
        return native_error(
            PlatformError("UPSTREAM_INVALID_RESPONSE", "invalid vibe encoding returned", 502),
            request,
        )


@router.get("/ai/generate-image/suggest-tags")
async def native_suggest_tags(
    request: Request,
    prompt: str = Query(default=""),
    model: str = Query(default=""),
    _: str = Depends(require_client),
) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    async def call(lease):
        return await lease.client.raw_get(
            path="/ai/generate-image/suggest-tags",
            params={"prompt": prompt, **({"model": model} if model else {})},
        )

    try:
        content, content_type = await run_tool(
            db,
            scheduler,
            operation="suggest-tags",
            call=call,
            language=language,
        )
    except PlatformError as exc:
        return native_error(exc, request)
    return Response(content=content, media_type=content_type.split(";", 1)[0])


@router.post("/upload/image")
async def native_upload_image(
    request: Request,
    file: UploadFile = File(...),
    _: str = Depends(require_client),
) -> JSONResponse:
    from ..image_store import image_store

    raw = await file.read()
    try:
        entry = image_store.put(raw, mime_hint=file.content_type or "image/png")
    except ValueError as exc:
        raise PlatformError("PAYLOAD_TOO_LARGE", str(exc), 413) from exc
    return JSONResponse(
        content={
            "upload_id": entry.upload_id,
            "sha256": entry.sha256,
            "width": entry.width,
            "height": entry.height,
            "size_bytes": entry.size_bytes,
            "mime_type": entry.mime_type,
        }
    )


# Exposed for the launcher router, which reuses the same tool passthrough logic.
__all__ = ["router", "native_error", "ALLOWED_NATIVE_OPERATIONS"]
