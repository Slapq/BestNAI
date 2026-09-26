"""YesNAI and [OI]-compatible surfaces under `/v1`.

* ``POST /v1/nai/generate-image`` — YesNAI JSON/Base64 generation
* ``POST /v1/images/generations`` — [OI] Images compatibility
* ``POST /v1/chat/completions`` — [OI] Chat compatibility (BestNAI payload in the
  first user message)
* ``GET  /v1/models`` — published model list

Errors use the [OI] envelope `{"error": {"message", "type", "code"}}`.
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..auth import require_client
from ..errors import PlatformError, openai_error_payload
from ..execution import prepare_generation, run_generation
from ..generation import (
    normalize_native_payload,
    normalize_openai_images_payload,
)
from ..key_policy import SUPPORTED_NAI_MODELS
from .deps import (
    get_db,
    get_scheduler,
    idempotency_key_of,
    request_id_of,
    request_language,
    resolve_upload_refs,
)
from .responses import image_base64, normalize_image_format

router = APIRouter(prefix="/v1", tags=["ynai"])

#: Models advertised by `/v1/models`.  Callers should treat this as the platform
#: catalogue; administrators may restrict it further through pricing rules.
PUBLISHED_MODELS: tuple[str, ...] = SUPPORTED_NAI_MODELS


def openai_error(exc: PlatformError, request: Request) -> JSONResponse:
    return JSONResponse(
        content=openai_error_payload(exc.code, exc.message),
        status_code=exc.status_code,
    )


def _openai_error_raw(
    message: str,
    status: int = 400,
    *,
    code: str | None = None,
    err_type: str | None = None,
) -> JSONResponse:
    resolved_type = err_type or ("invalid_request_error" if status < 500 else "server_error")
    return JSONResponse(
        content={"error": {"message": message, "type": resolved_type, "code": code or ""}},
        status_code=status,
    )


@router.get("/models")
async def list_models() -> JSONResponse:
    created = 1_700_000_000
    return JSONResponse(
        content={
            "object": "list",
            "data": [
                {"id": model, "object": "model", "created": created, "owned_by": "yesnai"}
                for model in PUBLISHED_MODELS
            ],
        }
    )


@router.post("/nai/generate-image")
async def ynai_generate_image(request: Request, _: str = Depends(require_client)) -> JSONResponse:
    """YesNAI JSON + Base64 PNG generation."""
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return _openai_error_raw("Request body must be a JSON object", 400)
    native_payload = normalize_native_payload(resolve_upload_refs(body))
    fmt = normalize_image_format(request.query_params.get("image_format"), "png")

    try:
        prepared = await prepare_generation(
            db,
            entrypoint="ynai",
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
        images = await run_generation(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return openai_error(exc, request)

    from ..generation import job_summary

    refreshed = await db.fetchone(
        "SELECT * FROM generation_jobs WHERE job_id = ?", (prepared.job_id,)
    )
    return JSONResponse(
        content={
            "job": job_summary(refreshed) if refreshed else {"job_id": prepared.job_id},
            "images": [image_base64(image, fmt) for image in images],
            "image_format": fmt,
        }
    )


@router.post("/images/generations")
async def openai_images_generations(
    request: Request,
    _: str = Depends(require_client),
) -> JSONResponse:
    """[OI] Images compatibility."""
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return _openai_error_raw("Request body must be a JSON object", 400)

    response_format = str(body.get("response_format") or "b64_json").lower()
    if response_format not in {"b64_json", "url"}:
        return _openai_error_raw(
            "response_format must be 'b64_json' or 'url'", 400, code="INVALID_NATIVE_REQUEST"
        )

    try:
        native_payload = normalize_openai_images_payload(resolve_upload_refs(body))
    except PlatformError as exc:
        return openai_error(exc, request)

    fmt = normalize_image_format(request.query_params.get("image_format"), "png")

    try:
        prepared = await prepare_generation(
            db,
            entrypoint="images",
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
        images = await run_generation(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return openai_error(exc, request)

    from ..generation import job_summary

    refreshed = await db.fetchone(
        "SELECT * FROM generation_jobs WHERE job_id = ?", (prepared.job_id,)
    )
    data: list[dict[str, str]] = []
    for image in images:
        encoded = image_base64(image, fmt)
        if response_format == "url":
            # BestNAI returns an inline Data URL; it does not host public URLs.
            data.append({"url": f"data:image/{fmt};base64,{encoded}"})
        else:
            data.append({"b64_json": encoded})

    return JSONResponse(
        content={
            "created": int(time.time()),
            "data": data,
            "job": job_summary(refreshed) if refreshed else {"job_id": prepared.job_id},
        }
    )


def _extract_inner_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the BestNAI generation payload out of the first user message."""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        raw = message.get("content", "")
        if isinstance(raw, list):
            text = ""
            for part in raw:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    break
            raw = text
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {"prompt": raw}
        if not isinstance(parsed, dict):
            parsed = {"prompt": str(parsed)}
        if "model" not in parsed and "model" in payload:
            parsed["model"] = payload["model"]
        return parsed
    return None


@router.post("/chat/completions")
async def openai_chat_completions(
    request: Request,
    _: str = Depends(require_client),
) -> Any:
    """[OI] Chat compatibility.

    The inner BestNAI/YesNAI generation payload is passed as a JSON string in the
    first user message.  `stream=true` returns a buffered BestNAI-style SSE
    response: it does not relay upstream generation progress.
    """
    db = get_db(request)
    scheduler = get_scheduler(request)
    language = request_language(request)

    body = await request.json()
    if not isinstance(body, dict):
        return _openai_error_raw("Request body must be a JSON object", 400)

    inner = _extract_inner_payload(body)
    if inner is None:
        return _openai_error_raw(
            "No user message with text content found", 400, code="INVALID_NATIVE_REQUEST"
        )
    if not inner.get("model"):
        return _openai_error_raw(
            "model is required (outer body or inner payload)", 400, code="INVALID_NATIVE_REQUEST"
        )
    if not inner.get("prompt") and not inner.get("input"):
        return _openai_error_raw(
            "prompt is required in the inner payload", 400, code="INVALID_NATIVE_REQUEST"
        )

    try:
        native_payload = normalize_native_payload(resolve_upload_refs(inner))
    except PlatformError as exc:
        return openai_error(exc, request)

    fmt = normalize_image_format(request.query_params.get("image_format"), "png")

    try:
        prepared = await prepare_generation(
            db,
            entrypoint="chat",
            native_payload=native_payload,
            idempotency_key=idempotency_key_of(request),
            request_id=request_id_of(request),
        )
        images = await run_generation(db, scheduler, prepared, language=language)
    except PlatformError as exc:
        return openai_error(exc, request)

    content = "\n".join(
        f"![image_{index}](data:image/{fmt};base64,{image_base64(image, fmt)})"
        for index, image in enumerate(images)
    )
    created = int(time.time())
    chat_id = f"bestnai-{prepared.job_id[:16]}"
    model_name = prepared.job_fields["model"]

    stream_requested = body.get("stream") is True or str(body.get("stream", "")).lower() == "true"
    if stream_requested:
        async def chat_stream() -> AsyncGenerator[str, None]:
            def frame(delta: dict[str, Any], finish: str | None) -> str:
                return "data: " + json.dumps(
                    {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                ) + "\n\n"

            yield frame({"role": "assistant", "content": ""}, None)
            yield frame({"content": content}, None)
            yield frame({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(chat_stream(), media_type="text/event-stream")

    from ..generation import job_summary

    refreshed = await db.fetchone(
        "SELECT * FROM generation_jobs WHERE job_id = ?", (prepared.job_id,)
    )
    return JSONResponse(
        content={
            "id": chat_id,
            "object": "chat.completion",
            "created": created,
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "job": job_summary(refreshed) if refreshed else {"job_id": prepared.job_id},
        }
    )
