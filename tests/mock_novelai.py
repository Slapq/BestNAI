"""A stand-in for NovelAI's image API.

Used by the end-to-end test so the full path (SDK -> BestNAI -> "upstream") can
be exercised without real credentials or real generation cost.

Endpoints mirror what BestNAI's client calls:

* `GET  /user/subscription`, `GET /user/information`  — account snapshot
* `POST /ai/generate-image`                            — ZIP of one PNG
* `POST /ai/generate-image-stream`                     — length-prefixed MessagePack
* `POST /ai/encode-vibe`                               — raw bytes
* `POST /ai/upscale`, `/ai/augment-image`, `/ai/annotate-image`
* `GET  /ai/generate-image/suggest-tags`               — JSON tags
"""
from __future__ import annotations

import io
import json
import struct
import zipfile
from typing import Any

import msgpack
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image

app = FastAPI(title="Mock NovelAI")

#: Request counter, exposed at /__stats for assertions.
STATS: dict[str, int] = {}
#: When set, the next matching request returns this error instead.
FAILURES: dict[str, dict[str, Any]] = {}

PNG_PIXEL = (255, 0, 0)


def _png_bytes(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), PNG_PIXEL).save(buffer, format="PNG")
    return buffer.getvalue()


def _zip_bytes(count: int = 1) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(count):
            archive.writestr(f"image_{index:02d}.png", _png_bytes())
    return buffer.getvalue()


def _frame(payload: dict[str, Any]) -> bytes:
    packed = msgpack.packb(payload, use_bin_type=True)
    return struct.pack(">I", len(packed)) + packed


def _record(path: str) -> None:
    STATS[path] = STATS.get(path, 0) + 1


def _failure_for(path: str) -> Response | None:
    rule = FAILURES.get(path)
    if not rule:
        return None
    return JSONResponse(
        content=rule.get("body", {"message": "upstream failure"}),
        status_code=int(rule.get("status", 500)),
    )


@app.get("/__stats")
async def stats() -> dict[str, Any]:
    return {"stats": dict(STATS), "failures": dict(FAILURES)}


@app.post("/__fail")
async def set_failure(payload: dict[str, Any]) -> dict[str, Any]:
    """Arm a failure for one path: `{"path": ..., "status": 402, "body": {...}}`."""
    path = str(payload.get("path") or "")
    if payload.get("clear"):
        FAILURES.pop(path, None)
    else:
        FAILURES[path] = {"status": payload.get("status", 500), "body": payload.get("body", {})}
    return {"failures": dict(FAILURES)}


@app.post("/__reset")
async def reset() -> dict[str, Any]:
    STATS.clear()
    FAILURES.clear()
    return {"ok": True}


@app.get("/user/subscription")
async def subscription(request: Request) -> Response:
    _record("/user/subscription")
    failure = _failure_for("/user/subscription")
    if failure is not None:
        return failure
    return JSONResponse(
        content={
            "tier": 3,
            "active": True,
            "expiresAt": "2030-01-01T00:00:00Z",
            "trainingStepsLeft": {"fixedTrainingStepsLeft": 1000, "purchasedTrainingSteps": 2500},
            "usage": {"percent": 80.0, "isNegative": False},
            "isGracePeriod": False,
        }
    )


@app.get("/user/information")
async def information(request: Request) -> Response:
    _record("/user/information")
    return JSONResponse(
        content={
            "emailVerified": True,
            "trialActivated": True,
            "trialImagesLeft": 0,
            "banStatus": "",
        }
    )


@app.post("/ai/generate-image")
async def generate_image(request: Request) -> Response:
    _record("/ai/generate-image")
    failure = _failure_for("/ai/generate-image")
    if failure is not None:
        return failure
    body = await request.json()
    count = int((body.get("parameters") or {}).get("n_samples") or 1)
    return Response(
        content=_zip_bytes(count),
        media_type="application/zip",
    )


@app.post("/ai/generate-image-stream")
async def generate_image_stream(request: Request) -> Response:
    _record("/ai/generate-image-stream")
    failure = _failure_for("/ai/generate-image-stream")
    if failure is not None:
        return failure
    import base64

    encoded = base64.b64encode(_png_bytes()).decode("ascii")
    frames = b"".join(
        [
            _frame({"event_type": "intermediate", "samp_ix": 0, "step_ix": 0, "gen_id": 1, "sigma": 12.5}),
            _frame({"event_type": "intermediate", "samp_ix": 0, "step_ix": 1, "gen_id": 1, "sigma": 3.5}),
            _frame({"event_type": "final", "samp_ix": 0, "step_ix": 28, "gen_id": 1, "image": encoded}),
        ]
    )
    return Response(content=frames, media_type="application/x-msgpack")


@app.post("/ai/encode-vibe")
async def encode_vibe(request: Request) -> Response:
    _record("/ai/encode-vibe")
    failure = _failure_for("/ai/encode-vibe")
    if failure is not None:
        return failure
    return Response(content=b"encoded-vibe-bytes", media_type="application/octet-stream")


@app.post("/ai/upscale")
async def upscale(request: Request) -> Response:
    _record("/ai/upscale")
    failure = _failure_for("/ai/upscale")
    if failure is not None:
        return failure
    return Response(content=_zip_bytes(1), media_type="application/zip")


@app.post("/ai/augment-image")
async def augment_image(request: Request) -> Response:
    _record("/ai/augment-image")
    return Response(content=_zip_bytes(1), media_type="application/zip")


@app.post("/ai/annotate-image")
async def annotate_image(request: Request) -> Response:
    _record("/ai/annotate-image")
    return Response(content=_zip_bytes(1), media_type="application/zip")


@app.get("/ai/generate-image/suggest-tags")
async def suggest_tags(prompt: str = "", model: str = "") -> Response:
    _record("/ai/generate-image/suggest-tags")
    failure = _failure_for("/ai/generate-image/suggest-tags")
    if failure is not None:
        return failure
    return JSONResponse(content={"tags": [f"{prompt}tag1", f"{prompt}tag2"]})


@app.post("/ai/generate-image/request-price")
async def request_price(payload: dict[str, Any]) -> Response:
    _record("/ai/generate-image/request-price")
    return JSONResponse(content={"price": 0})


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=18901, log_level="warning")
