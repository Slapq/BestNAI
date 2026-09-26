"""BestNAI-native compatibility surface.

These are BestNAI's original endpoints, retained so existing integrations keep
working.  They now run through the same pricing, key pool, and queue path as the
YesNAI surfaces, so behaviour is consistent across dialects.

Where an endpoint name collides with the YesNAI contract (`/ai/generate-image`,
`/ai/generate-image-stream`, `/ai/encode-vibe`) the YesNAI meaning wins and the
BestNAI variant lives under `/v1/...` only.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image as PILImage

from ..auth import require_client
from ..client import NovelAIClient, NovelAIAPIError
from ..errors import PlatformError, error_payload
from ..execution import (
    prepare_generation,
    run_generation,
    stream_generation,
    upstream_error,
)
from ..generation import normalize_native_payload
from ..service import enforce_alias_limit, estimate_anlas_breakdown
from .deps import (
    get_db,
    get_scheduler,
    get_settings,
    idempotency_key_of,
    request_id_of,
    request_language,
    resolve_upload_refs,
)
from .responses import image_response, normalize_image_format

router = APIRouter(tags=["legacy"])


def _legacy_error(exc: PlatformError) -> JSONResponse:
    return JSONResponse(
        content=error_payload(exc.code, exc.message, exc.status_code, exc.details),
        status_code=exc.status_code,
    )


# ── Upload ────────────────────────────────────────────────────────────────────


@router.post("/v1/upload/image")
async def upload_image(
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


# ── Legacy estimation ─────────────────────────────────────────────────────────
#
# These endpoints keep their historical paths and response shape, but the numbers
# now come from the same estimator that prices requests, so a quote and the
# eventual charge cannot disagree.


def _legacy_estimate(request: Request, payload: dict) -> JSONResponse:
    settings = get_settings(request)
    if not isinstance(payload, dict):
        raise PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
    info = estimate_anlas_breakdown(
        payload, default_max_anlas=settings.default_max_anlas
    )
    return JSONResponse(content=info)


@router.post("/v1/estimate/high-level")
async def legacy_estimate_high_level(
    payload: dict,
    request: Request,
    _: str = Depends(require_client),
) -> JSONResponse:
    return _legacy_estimate(request, payload)


@router.post("/v1/estimate/low-level")
async def legacy_estimate_low_level(
    payload: dict,
    request: Request,
    _: str = Depends(require_client),
) -> JSONResponse:
    return _legacy_estimate(request, payload)


@router.post("/v1/estimate/byok")
async def legacy_estimate_byok(
    payload: dict,
    request: Request,
    _: str = Depends(require_client),
) -> JSONResponse:
    return _legacy_estimate(request, payload)


@router.post("/ai/estimate-image")
async def legacy_estimate_native(
    payload: dict,
    request: Request,
    _: str = Depends(require_client),
) -> JSONResponse:
    return _legacy_estimate(request, payload)


# ── Legacy generation ─────────────────────────────────────────────────────────


@router.post("/v1/generate/high-level")
async def legacy_generate_high_level(
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    return await _legacy_generate(request, entrypoint="legacy-high-level")


@router.post("/v1/generate/low-level")
async def legacy_generate_low_level(
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    return await _legacy_generate(request, entrypoint="legacy-low-level")


async def _legacy_generate(request: Request, *, entrypoint: str) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return _legacy_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
        )
    resolved = resolve_upload_refs(body)

    # The `-anlas-N` alias is resolved centrally, in payload normalization.
    try:
        native_payload = normalize_native_payload(resolved)
    except PlatformError as exc:
        return _legacy_error(exc)
    requested_model = native_payload.get("model")

    fmt = normalize_image_format(request.query_params.get("image_format"), "png")
    return_format = request.query_params.get("return_format", "json")

    try:
        prepared = await prepare_generation(
            db,
            entrypoint=entrypoint,
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
    except PlatformError as exc:
        # A failure here means no job row was created, so there is nothing to settle.
        return _legacy_error(exc)

    try:
        images = await run_generation(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return _legacy_error(exc)

    if return_format in {"zip", "binary", "auto"}:
        return image_response(
            images,
            fmt=fmt,
            response_format=return_format,
            metadata={
                "job_id": prepared.job_id,
                "requested_model": requested_model,
                "resolved_model": prepared.job_fields["model"],
                "estimated_anlas": prepared.estimated_anlas,
                "cost_gems": prepared.cost_gems,
                "image_format": fmt,
                "image_count": len(images),
            },
            headers={"X-YesNAI-Gems-Charged": str(prepared.cost_gems)},
        )

    from .responses import image_base64

    return JSONResponse(
        content={
            "job": {
                "job_id": prepared.job_id,
                "request_id": prepared.request_id,
                "model": prepared.job_fields["model"],
                "action": prepared.job_fields["action"],
                "width": prepared.job_fields["width"],
                "height": prepared.job_fields["height"],
                "steps": prepared.job_fields["steps"],
                "n_samples": prepared.job_fields["n_samples"],
                "cost_gems": prepared.cost_gems,
                "estimated_anlas": prepared.estimated_anlas,
                "status": "succeeded",
            },
            "requested_model": requested_model,
            "resolved_model": prepared.job_fields["model"],
            "image_format": fmt,
            "images": [image_base64(image, fmt) for image in images],
        }
    )


@router.post("/v1/generate/high-level-stream")
async def legacy_generate_high_level_stream(
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    return await _legacy_generate_stream(request, entrypoint="legacy-high-level-stream")


@router.post("/v1/generate/low-level-stream")
async def legacy_generate_low_level_stream(
    request: Request,
    _: str = Depends(require_client),
) -> Response:
    return await _legacy_generate_stream(request, entrypoint="legacy-low-level-stream")


async def _legacy_generate_stream(request: Request, *, entrypoint: str) -> Response:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return _legacy_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
        )
    try:
        native_payload = normalize_native_payload(resolve_upload_refs(body))
    except PlatformError as exc:
        return _legacy_error(exc)

    try:
        prepared = await prepare_generation(
            db,
            entrypoint=entrypoint,
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
    except PlatformError as exc:
        return _legacy_error(exc)

    async def events() -> AsyncGenerator[str, None]:
        try:
            async for chunk in stream_generation(db, scheduler, prepared, language=language):
                yield f"data: {json.dumps(chunk.raw or {}, ensure_ascii=False)}\n\n"
        except PlatformError as exc:
            yield "event: error\n"
            yield f"data: {json.dumps(error_payload(exc.code, exc.message, exc.status_code), ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "X-Estimated-Anlas": str(prepared.estimated_anlas),
            "X-YesNAI-Gems-Charged": str(prepared.cost_gems),
            "X-Resolved-Model": prepared.job_fields["model"],
        },
    )


@router.post("/v1/encode-vibe")
async def legacy_encode_vibe(request: Request, _: str = Depends(require_client)) -> JSONResponse:
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)
    from ..execution import run_tool

    body = await request.json()
    if not isinstance(body, dict):
        return _legacy_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
        )
    resolved = resolve_upload_refs(body)
    image = str(resolved.get("image") or "")
    if not image:
        return _legacy_error(PlatformError("INVALID_NATIVE_REQUEST", "image is required", 400))

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
        return _legacy_error(exc)
    return JSONResponse(content={"data": encoded})


# ── BYOK (bring your own NovelAI key) ─────────────────────────────────────────


def _user_key(request: Request) -> str:
    key = request.headers.get("X-NAI-Key", "").strip()
    if not key:
        raise PlatformError("INVALID_API_TOKEN", "X-NAI-Key header required", 401)
    if not key.startswith("pst-"):
        raise PlatformError("INVALID_API_TOKEN", "Invalid NAI API key format", 401)
    return key


@router.post("/v1/generate/byok")
async def legacy_generate_byok(request: Request, _: str = Depends(require_client)) -> Response:
    settings = get_settings(request)
    language = request_language(request)
    user_key = _user_key(request)

    body = await request.json()
    if not isinstance(body, dict):
        return _legacy_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
        )
    try:
        native_payload = normalize_native_payload(resolve_upload_refs(body))
    except PlatformError as exc:
        return _legacy_error(exc)

    fmt = normalize_image_format(request.query_params.get("image_format"), "png")
    return_format = request.query_params.get("return_format", "json")

    client = NovelAIClient(
        user_key,
        image_base=settings.novelai_image_base,
        api_base=settings.novelai_api_base,
    )
    try:
        images = await client.generate_image(native_payload, official_price_authorized=True)
    except NovelAIAPIError as exc:
        return _legacy_error(upstream_error(exc, language=language))
    finally:
        await client.close()

    return image_response(
        images,
        fmt=fmt,
        response_format=return_format,
        metadata={"image_format": fmt, "image_count": len(images), "mode": "byok"},
    )


@router.post("/v1/generate/byok-stream")
async def legacy_generate_byok_stream(request: Request, _: str = Depends(require_client)) -> Response:
    settings = get_settings(request)
    language = request_language(request)
    user_key = _user_key(request)

    body = await request.json()
    if not isinstance(body, dict):
        return _legacy_error(
            PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)
        )
    try:
        native_payload = normalize_native_payload(resolve_upload_refs(body))
    except PlatformError as exc:
        return _legacy_error(exc)

    async def events() -> AsyncGenerator[str, None]:
        client = NovelAIClient(
            user_key,
            image_base=settings.novelai_image_base,
            api_base=settings.novelai_api_base,
        )
        try:
            async for chunk in client.generate_image_stream(
                native_payload, official_price_authorized=True
            ):
                yield f"data: {json.dumps(chunk.raw or {}, ensure_ascii=False)}\n\n"
        except NovelAIAPIError as exc:
            err = upstream_error(exc, language=language)
            yield "event: error\n"
            yield f"data: {json.dumps(error_payload(err.code, err.message, err.status_code), ensure_ascii=False)}\n\n"
        finally:
            await client.close()

    return StreamingResponse(events(), media_type="text/event-stream")
