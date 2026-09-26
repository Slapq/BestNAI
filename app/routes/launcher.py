"""Aaalice Launcher compatibility surface (root `/ai/*`, `/user/subscription`).

This is the BestNAI/YesNAI extension dialect: the same native request bodies as
`/native/*`, but with ZIP and MessagePack responses plus a quote endpoint.
Errors use the launcher envelope `{"message", "code"}`.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, UploadFile, File
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..auth import require_client
from ..errors import PlatformError, launcher_error_payload
from ..execution import (
    fail_job,
    open_raw_stream,
    prepare_generation,
    run_generation,
    run_tool,
    succeed_job,
)
from ..generation import normalize_native_payload
from ..pool import pool_anlas
from ..pricing_store import quote_generation
from .deps import (
    get_db,
    get_scheduler,
    idempotency_key_of,
    request_id_of,
    request_language,
    resolve_upload_refs,
)
from .responses import GEMS_CHARGED_HEADER, image_response, normalize_image_format

router = APIRouter(tags=["launcher"])


def launcher_error(exc: PlatformError, request: Request) -> JSONResponse:
    return JSONResponse(
        content=launcher_error_payload(exc.code, exc.message),
        status_code=exc.status_code,
    )


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
async def launcher_subscription(request: Request, _: str = Depends(require_client)) -> JSONResponse:
    return JSONResponse(content=_pool_subscription(get_scheduler(request), include_extensions=True))


@router.post("/ai/generate-image/quote")
async def launcher_quote(request: Request, _: str = Depends(require_client)) -> Response:
    db = get_db(request)
    body = await request.json()
    if not isinstance(body, dict):
        return launcher_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400),
            request,
        )
    resolved = resolve_upload_refs(body)
    native_payload = normalize_native_payload(resolved)
    try:
        quote = await quote_generation(db, native_payload)
    except PlatformError as exc:
        return launcher_error(exc, request)
    return JSONResponse(content=quote)


@router.post("/ai/generate-image")
async def launcher_generate(request: Request, _: str = Depends(require_client)) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return launcher_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400),
            request,
        )
    native_payload = normalize_native_payload(resolve_upload_refs(body))
    fmt = normalize_image_format(request.query_params.get("image_format"), "png")

    try:
        prepared = await prepare_generation(
            db,
            entrypoint="launcher",
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
        images = await run_generation(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return launcher_error(exc, request)

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
async def launcher_generate_stream(request: Request, _: str = Depends(require_client)) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return launcher_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400),
            request,
        )
    native_payload = normalize_native_payload(resolve_upload_refs(body))

    try:
        prepared = await prepare_generation(
            db,
            entrypoint="launcher-stream",
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
        upstream, lease = await open_raw_stream(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return launcher_error(exc, request)

    async def body_stream() -> AsyncIterator[bytes]:
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
        body_stream(),
        status_code=upstream.response.status_code,
        media_type=media_type,
        headers={GEMS_CHARGED_HEADER: str(prepared.cost_gems)},
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

_TOOL_TARGETS: dict[str, tuple[str, str]] = {
    "upscale": ("main", "/ai/upscale"),
    "augment-image": ("image", "/ai/augment-image"),
    "annotate-image": ("image", "/ai/annotate-image"),
}


def _validate_tool_payload(operation: str, body: dict[str, Any]) -> None:
    """Validate tool inputs the way YesNAI does, before leasing a key."""
    image = body.get("image")
    if not isinstance(image, str) or not image:
        raise PlatformError("INVALID_NATIVE_REQUEST", "image is required", 400)
    if operation == "upscale":
        try:
            scale = int(body.get("scale", 0))
        except (TypeError, ValueError):
            scale = 0
        if scale not in {2, 4}:
            raise PlatformError("INVALID_NATIVE_REQUEST", "upscale scale must be 2 or 4", 400)
    elif operation in {"augment-image", "annotate-image"}:
        req_type = body.get("req_type")
        if not isinstance(req_type, str) or not req_type.strip():
            raise PlatformError("INVALID_NATIVE_REQUEST", "req_type is required", 400)


async def _launcher_tool(operation: str, request: Request) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)
    api_host, path = _TOOL_TARGETS[operation]

    body = await request.json()
    if not isinstance(body, dict):
        return launcher_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400),
            request,
        )
    try:
        _validate_tool_payload(operation, body)
    except PlatformError as exc:
        return launcher_error(exc, request)
    resolved = resolve_upload_refs(body)

    async def call(lease):
        return await lease.client.raw_request(
            path=path,
            payload=resolved,
            api_host=api_host,
            accept=request.headers.get("accept"),
        )

    try:
        content, content_type = await run_tool(
            db, scheduler, operation=operation, call=call, language=language
        )
    except PlatformError as exc:
        return launcher_error(exc, request)
    return Response(content=content, media_type=content_type.split(";", 1)[0])


@router.post("/ai/upscale")
async def launcher_upscale(request: Request, _: str = Depends(require_client)) -> Response:
    return await _launcher_tool("upscale", request)


@router.post("/ai/augment-image")
async def launcher_augment(request: Request, _: str = Depends(require_client)) -> Response:
    return await _launcher_tool("augment-image", request)


@router.post("/ai/annotate-image")
async def launcher_annotate(request: Request, _: str = Depends(require_client)) -> Response:
    return await _launcher_tool("annotate-image", request)


@router.post("/ai/encode-vibe")
async def launcher_encode_vibe(request: Request, _: str = Depends(require_client)) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return launcher_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400),
            request,
        )
    resolved = resolve_upload_refs(body)
    image = str(resolved.get("image") or "")
    if not image:
        return launcher_error(
            PlatformError("INVALID_NATIVE_REQUEST", "image is required", 400), request
        )

    async def call(lease):
        return await lease.client.encode_vibe(
            image_b64=image,
            information_extracted=float(resolved.get("information_extracted", 1.0)),
            model=str(resolved.get("model") or ""),
        )

    try:
        encoded = await run_tool(
            db, scheduler, operation="encode-vibe", call=call, language=language
        )
    except PlatformError as exc:
        return launcher_error(exc, request)
    return JSONResponse(content={"data": encoded})


@router.get("/ai/generate-image/suggest-tags")
async def launcher_suggest_tags(
    request: Request,
    prompt: str = Query(default=""),
    model: str = Query(default=""),
    _: str = Depends(require_client),
) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    if len(prompt.strip()) < 2:
        return JSONResponse(content={"tags": []})

    async def call(lease):
        return await lease.client.raw_get(
            path="/ai/generate-image/suggest-tags",
            params={"prompt": prompt, **({"model": model} if model else {})},
        )

    try:
        content, _content_type = await run_tool(
            db, scheduler, operation="suggest-tags", call=call, language=language
        )
    except PlatformError:
        # Tag suggestion is advisory; degrade to an empty result.
        return JSONResponse(content={"tags": []})

    import json as _json

    try:
        parsed = _json.loads(content)
    except (TypeError, ValueError):
        return JSONResponse(content={"tags": []})
    if isinstance(parsed, dict) and "tags" in parsed:
        return JSONResponse(content=parsed)
    return JSONResponse(content={"tags": parsed if isinstance(parsed, list) else []})


@router.post("/ai/upload/image")
async def launcher_upload_image(
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
