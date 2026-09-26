"""FastAPI app for a multi-key NovelAI gateway."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator, TypeVar

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from novelai.exceptions import NovelAIError
from novelai.gateway.config import GatewaySettings
from novelai.gateway.models import (
    EstimateResponse,
    GatewayHealthResponse,
    GenerateImageResponse,
    encode_image_to_base64,
    parse_model_alias,
)
from novelai.gateway.scheduler import KeyScheduler
from novelai.types.api.image import (
    EncodeVibeRequest,
    ImageGenerationRequest,
    StreamImageGenerationRequest,
)
from novelai.utils.anlas import AnlasEstimate, calculate_anlas

load_dotenv()

RequestModelT = TypeVar(
    "RequestModelT", ImageGenerationRequest, StreamImageGenerationRequest
)


def _estimate_request(
    request: ImageGenerationRequest | StreamImageGenerationRequest,
) -> EstimateResponse:
    parsed = parse_model_alias(request.model, request.action)

    try:
        estimate = calculate_anlas(
            parsed.resolved_model,
            request.parameters,
            is_opus=True,
        )
    except (NotImplementedError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return EstimateResponse(
        requested_model=parsed.requested_model,
        resolved_model=parsed.resolved_model,
        max_points=parsed.max_points,
        allowed=estimate.total_anlas <= parsed.max_points,
        estimated_anlas=estimate,
    )


def _enforce_points_limit(estimate_response: EstimateResponse) -> AnlasEstimate:
    estimate = estimate_response.estimated_anlas
    if estimate.total_anlas > estimate_response.max_points:
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Estimated Anlas exceeds the model point limit",
                "estimated_anlas": estimate.total_anlas,
                "max_points": estimate_response.max_points,
                "resolved_model": estimate_response.resolved_model,
            },
        )
    return estimate


def _rewrite_model(
    request: RequestModelT,
    resolved_model: str,
) -> RequestModelT:
    return request.model_copy(update={"model": resolved_model})


def _stream_headers(estimate_response: EstimateResponse) -> dict[str, str]:
    estimate = estimate_response.estimated_anlas
    return {
        "Cache-Control": "no-cache",
        "X-Estimated-Anlas": str(estimate.total_anlas),
        "X-Max-Points": str(estimate_response.max_points),
        "X-Resolved-Model": estimate_response.resolved_model,
        "X-Opus-Assumed": "true",
    }


def _http_error_from_sdk(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, NovelAIError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def _scheduler(request: Request) -> KeyScheduler:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=500, detail="Gateway scheduler is not ready")
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = GatewaySettings.from_env()
    scheduler = KeyScheduler(
        api_keys=settings.api_keys,
        queue_timeout_seconds=settings.queue_timeout_seconds,
    )
    app.state.settings = settings
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        await scheduler.close()


app = FastAPI(
    title="NovelAI Multi-Key Gateway",
    summary="Round-robin, single-flight gateway with SDK-based point gating.",
    lifespan=lifespan,
)


@app.get("/health", response_model=GatewayHealthResponse)
async def health(request: Request) -> GatewayHealthResponse:
    scheduler = _scheduler(request)
    settings: GatewaySettings = request.app.state.settings
    return GatewayHealthResponse(
        total_keys=scheduler.total_keys,
        busy_keys=scheduler.busy_keys,
        idle_keys=scheduler.idle_keys,
        queued_requests=scheduler.queued_requests,
        queue_timeout_seconds=settings.queue_timeout_seconds,
    )


@app.post("/ai/estimate-image", response_model=EstimateResponse)
async def estimate_image(
    payload: ImageGenerationRequest,
) -> EstimateResponse:
    return _estimate_request(payload)


@app.post("/ai/estimate-image-stream", response_model=EstimateResponse)
async def estimate_image_stream(
    payload: StreamImageGenerationRequest,
) -> EstimateResponse:
    return _estimate_request(payload)


@app.post("/ai/generate-image", response_model=GenerateImageResponse)
async def generate_image(
    payload: ImageGenerationRequest,
    request: Request,
) -> GenerateImageResponse:
    estimate_response = _estimate_request(payload)
    estimate = _enforce_points_limit(estimate_response)
    scheduler = _scheduler(request)

    try:
        lease = await scheduler.acquire()
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="负载过高，请稍后重试") from exc

    async with lease:
        try:
            upstream_request = _rewrite_model(payload, estimate_response.resolved_model)
            images = await lease.client.api_client.image.generate(upstream_request)
        except Exception as exc:
            raise _http_error_from_sdk(exc) from exc

    image_format = payload.parameters.image_format or "png"
    encoded_images = [
        encode_image_to_base64(image, image_format=image_format) for image in images
    ]

    return GenerateImageResponse(
        requested_model=estimate_response.requested_model,
        resolved_model=estimate_response.resolved_model,
        max_points=estimate_response.max_points,
        estimated_anlas=estimate,
        image_format=image_format,
        images=encoded_images,
    )


@app.post("/ai/generate-image-stream")
async def generate_image_stream(
    payload: StreamImageGenerationRequest,
    request: Request,
) -> StreamingResponse:
    estimate_response = _estimate_request(payload)
    _enforce_points_limit(estimate_response)
    scheduler = _scheduler(request)

    try:
        lease = await scheduler.acquire()
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="负载过高，请稍后重试") from exc

    upstream_request = _rewrite_model(payload, estimate_response.resolved_model)

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for chunk in lease.client.api_client.image.generate_stream(
                upstream_request
            ):
                yield f"data: {chunk.model_dump_json()}\n\n"
        except Exception as exc:
            error_payload = json.dumps({"detail": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {error_payload}\n\n"
        finally:
            await lease.release()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=_stream_headers(estimate_response),
    )


@app.post("/ai/encode-vibe")
async def encode_vibe(
    payload: EncodeVibeRequest,
    request: Request,
) -> dict[str, str]:
    """Expose the SDK low-level encode-vibe endpoint.

    This endpoint does not apply `-points-N` gating because the current SDK only
    exposes first-class Anlas estimation for generation requests. It still uses
    the shared multi-key scheduler to avoid concurrent upstream requests on the
    same key.
    """
    scheduler = _scheduler(request)

    try:
        lease = await scheduler.acquire()
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="负载过高，请稍后重试") from exc

    async with lease:
        try:
            vibe = await lease.client.api_client.image.encode_vibe(payload)
        except Exception as exc:
            raise _http_error_from_sdk(exc) from exc

    return {"data": vibe}


__all__ = ["app", "lifespan"]