"""Response construction helpers shared by the API surfaces."""
from __future__ import annotations

import base64
import io
import json
import zipfile
from typing import Any, Iterable

from fastapi.responses import JSONResponse, Response
from PIL import Image

IMAGE_FORMATS = frozenset({"png", "webp"})

#: Header exposing the Gems price of a completed generation.
GEMS_CHARGED_HEADER = "X-YesNAI-Gems-Charged"
#: Header exposing the estimated Anlas of a request.
ESTIMATED_ANLAS_HEADER = "X-YesNAI-Estimated-Anlas"


def normalize_image_format(value: Any, fallback: str = "png") -> str:
    return value if value in IMAGE_FORMATS else fallback


def image_bytes(image: Image.Image, fmt: str = "png") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt.upper())
    return buffer.getvalue()


def image_base64(image: Image.Image, fmt: str = "png") -> str:
    return base64.b64encode(image_bytes(image, fmt)).decode("utf-8")


def images_to_zip(
    images: Iterable[Image.Image],
    fmt: str = "png",
    *,
    metadata: dict[str, Any] | None = None,
) -> bytes:
    """Build a ZIP archive using YesNAI's entry naming.

    Entries are `image_0.png`, `image_1.png`, ... (index from 0), matching the
    reference implementation so clients that read entries by name work unchanged.
    """
    fmt = normalize_image_format(fmt)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, image in enumerate(images):
            archive.writestr(f"image_{index}.{fmt}", image_bytes(image, fmt))
        if metadata is not None:
            archive.writestr(
                "metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
            )
    return buffer.getvalue()


def image_response(
    images: list[Image.Image],
    *,
    fmt: str = "png",
    response_format: str = "json",
    metadata: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Render generated images as binary, ZIP, or JSON base64."""
    fmt = normalize_image_format(fmt)
    response_headers = dict(headers or {})
    if response_format == "binary" and len(images) == 1:
        return Response(image_bytes(images[0], fmt), media_type=f"image/{fmt}", headers=response_headers)
    if response_format == "zip" or (response_format == "auto" and len(images) > 1):
        response_headers.setdefault("Content-Disposition", "attachment; filename=images.zip")
        return Response(
            images_to_zip(images, fmt, metadata=metadata),
            media_type="application/zip",
            headers=response_headers,
        )
    return JSONResponse(
        content={
            "images": [image_base64(image, fmt) for image in images],
            "image_format": fmt,
            "metadata": metadata,
        },
        headers=response_headers,
    )


def json_error(status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(content=payload, status_code=status, headers=headers or {})


def ndjson_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def sse_event(payload: dict[str, Any], *, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(payload, ensure_ascii=False)}\n\n"
