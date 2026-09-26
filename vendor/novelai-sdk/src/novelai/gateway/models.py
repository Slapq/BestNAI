"""Shared gateway models and parsing helpers."""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from novelai.utils.anlas import AnlasEstimate

_POINTS_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?:-points-(?P<points>\d+))?$")


@dataclass(slots=True, frozen=True)
class ParsedModelAlias:
    """Model alias split into actual model name and platform point limit."""

    requested_model: str
    base_model: str
    resolved_model: str
    max_points: int


class EstimateResponse(BaseModel):
    """Point-estimation response returned by the gateway."""

    requested_model: str
    resolved_model: str
    max_points: int
    allowed: bool
    estimated_anlas: AnlasEstimate


class GenerateImageResponse(BaseModel):
    """HTTP-friendly image generation response."""

    requested_model: str
    resolved_model: str
    max_points: int
    estimated_anlas: AnlasEstimate
    image_format: Literal["png", "webp"]
    images: list[str] = Field(default_factory=list, description="Base64 image list")


class GatewayHealthResponse(BaseModel):
    """Basic scheduler state for health inspection."""

    total_keys: int
    busy_keys: int
    idle_keys: int
    queued_requests: int
    queue_timeout_seconds: float


def parse_model_alias(model: str | None, action: str | None) -> ParsedModelAlias:
    """Parse a model alias like `nai-diffusion-4-5-full-points-10`."""
    if not model:
        raise ValueError("The request must include a model name")

    match = _POINTS_SUFFIX_RE.match(model)
    if match is None:
        raise ValueError(f"Invalid model alias: {model}")

    base_model = match.group("base")
    points_text = match.group("points")
    max_points = int(points_text) if points_text is not None else 0

    resolved_model = base_model
    if action == "infill" and not resolved_model.endswith("-inpainting"):
        resolved_model = f"{resolved_model}-inpainting"

    return ParsedModelAlias(
        requested_model=model,
        base_model=base_model,
        resolved_model=resolved_model,
        max_points=max_points,
    )


def encode_image_to_base64(
    image: Image.Image,
    image_format: Literal["png", "webp"],
) -> str:
    """Encode a PIL image into base64 for JSON transport."""
    buffer = io.BytesIO()
    image.save(buffer, format=image_format.upper())
    return base64.b64encode(buffer.getvalue()).decode("utf-8")