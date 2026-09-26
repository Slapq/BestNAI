"""High-level convenience types.

These mirror the NovelAI SDK's user-facing models so callers can write
`GenerateImageParams(prompt=..., model=..., size=...)` and let the SDK build the
low-level payload.  The gateway accepts this shape directly, which means the SDK
and the server agree on what "high-level" means.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any, Literal

from PIL import Image as PILImage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ...exceptions import InvalidRequestError

from ...constants import (
    K_EULER_ANCESTRAL,
    NOISE_KARRAS,
    QUALITY_TAGS,
    UC_LIGHT,
    UNDESIRED_CONTENT_PRESETS,
    ImageSize,
    Sampler,
    UCPreset,
    NoiseSchedule,
    canonical_model_id,
    resolve_size,
    validate_size,
)

ImageInput = str | bytes | Path | PILImage.Image


def image_to_base64(image: ImageInput) -> str:
    """Encode an image input as raw base64 (no data-URI prefix).

    `str` inputs are passed through: they may already be base64 or an
    `upload_id:<id>` reference understood by the gateway.
    """
    if isinstance(image, str):
        return image
    if isinstance(image, Path):
        return base64.b64encode(image.read_bytes()).decode("ascii")
    if isinstance(image, bytes):
        return base64.b64encode(image).decode("ascii")
    if isinstance(image, PILImage.Image):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")
    raise TypeError(f"Unsupported image input type: {type(image)!r}")


class _ImageCarryingModel(BaseModel):
    """Base for models whose fields may hold PIL images, paths, or raw bytes."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Character(BaseModel):
    """A character with its own prompt and position."""

    prompt: str = Field(..., description="Character prompt")
    negative_prompt: str = Field(default="", description="Character undesired content")
    position: tuple[float, float] = Field(
        default=(0.5, 0.5), description="Character center as (x, y) in 0.0-1.0"
    )
    enabled: bool = Field(default=True, description="Enable this character")

    @field_validator("position", mode="after")
    @classmethod
    def _validate_position(cls, value: tuple[float, float]) -> tuple[float, float]:
        x, y = value
        if not (0.0 <= x <= 1.0) or not (0.0 <= y <= 1.0):
            raise ValueError("Position coordinates must be between 0.0 and 1.0")
        return value


class CharacterReference(_ImageCarryingModel):
    """A Precise Reference (Director tool) entry."""

    image: ImageInput = Field(..., description="Reference image")
    type: Literal["character", "style", "character&style"] = Field(
        default="character&style", description="Reference type"
    )
    fidelity: float = Field(default=1.0, ge=0.0, le=1.0, description="Reference fidelity")
    strength: float = Field(default=1.0, ge=0.0, le=1.0, description="Reference strength")


class ControlNetImage(_ImageCarryingModel):
    """A Vibe reference entry."""

    image: ImageInput = Field(..., description="Reference image")
    info_extracted: float = Field(default=0.7, ge=0.0, le=1.0, description="Information extracted")
    strength: float = Field(default=0.6, ge=0.0, le=1.0, description="Reference strength")


class ControlNet(BaseModel):
    """ControlNet (Vibe transfer) configuration."""

    images: list[ControlNetImage] = Field(..., description="Vibe reference images")
    strength: float = Field(default=1.0, ge=0.0, le=1.0, description="Overall strength")


class I2iParams(_ImageCarryingModel):
    """img2img inputs."""

    image: ImageInput = Field(..., description="Source image")
    strength: float = Field(default=0.7, ge=0.0, le=1.0, description="img2img strength")


class InpaintParams(_ImageCarryingModel):
    """Inpainting inputs."""

    image: ImageInput = Field(..., description="Source image")
    mask: ImageInput = Field(..., description="Mask image")
    strength: float = Field(default=1.0, ge=0.0, le=1.0, description="Inpaint strength")


class GenerateImageParams(_ImageCarryingModel):
    """High-level generation parameters.

    This is the convenience form: the SDK/gateway derives the low-level payload
    (quality tags, UC preset, V4 prompt structure) from it.
    """

    prompt: str = Field(..., description="Positive prompt")
    model: str = Field(default="nai-diffusion-4-5-full", description="Model ID")
    size: ImageSize = Field(default="portrait", description="Size preset or (width, height)")
    steps: int = Field(default=28, ge=1, le=50, description="Sampling steps")
    n_samples: int = Field(default=1, ge=1, le=8, description="Images to generate")
    sampler: Sampler = Field(default=K_EULER_ANCESTRAL, description="Sampling method")
    scale: float = Field(default=5.0, ge=0.0, le=10.0, description="Prompt guidance")
    seed: int | None = Field(default=None, description="Random seed")
    negative_prompt: str = Field(default="", description="Negative prompt")
    quality: bool = Field(default=True, description="Enable quality tags")
    uc_preset: UCPreset = Field(default=UC_LIGHT, description="Undesired content preset")
    noise_schedule: NoiseSchedule = Field(default=NOISE_KARRAS, description="Noise schedule")
    cfg_rescale: float = Field(default=0.0, ge=0.0, le=1.0, description="CFG rescale")
    variety_boost: bool = Field(default=False, description="Variety Boost")

    # Features
    i2i: I2iParams | None = Field(default=None, description="img2img inputs")
    inpaint: InpaintParams | None = Field(default=None, description="Inpainting inputs")
    controlnet: ControlNet | None = Field(default=None, description="Vibe transfer inputs")
    character_references: list[CharacterReference] | None = Field(
        default=None, description="Precise Reference inputs"
    )
    characters: list[Character] | None = Field(default=None, description="Character prompts")

    @field_validator("model", mode="after")
    @classmethod
    def _canonical_model(cls, value: str) -> str:
        return canonical_model_id(value)

    def resolved_size(self) -> tuple[int, int]:
        """Resolve and validate the requested size.

        Raises `InvalidRequestError` so callers can handle local validation and
        gateway rejections with a single error family.
        """
        try:
            width, height = resolve_size(self.size)
            validate_size(width, height)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        return width, height

    def effective_negative_prompt(self) -> str:
        """The negative prompt after applying the UC preset and quality tags."""
        preset = UNDESIRED_CONTENT_PRESETS.get(self.uc_preset, "")
        parts = [part.strip() for part in (preset, self.negative_prompt) if part and part.strip()]
        return ", ".join(parts)

    def effective_prompt(self) -> str:
        """The prompt after applying quality tags."""
        if not self.quality:
            return self.prompt
        return f"{self.prompt}, {QUALITY_TAGS}"


class GenerateImageStreamParams(GenerateImageParams):
    """High-level parameters for a streaming request."""

    stream: bool = Field(default=True, description="Enable streaming")


class UpscaleParams(_ImageCarryingModel):
    """Upscale request."""

    image: ImageInput = Field(..., description="Source image")
    scale: int = Field(default=2, description="Upscale factor (2 or 4)")
    model: str = Field(default="nai-diffusion-4-5-full", description="Model ID")


class AugmentParams(_ImageCarryingModel):
    """Augment-image request."""

    image: ImageInput = Field(..., description="Source image")
    req_type: str = Field(..., description="Augment operation")
    model: str = Field(default="nai-diffusion-4-5-full", description="Model ID")


class AnnotateParams(_ImageCarryingModel):
    """Annotate-image request."""

    image: ImageInput = Field(..., description="Source image")
    req_type: str = Field(..., description="Annotate operation")
    model: str = Field(default="nai-diffusion-4-5-full", description="Model ID")


class EncodeVibeParams(_ImageCarryingModel):
    """Vibe encoding request."""

    image: ImageInput = Field(..., description="Source image")
    information_extracted: float = Field(default=1.0, ge=0.01, le=1.0, description="Extraction strength")
    model: str = Field(default="nai-diffusion-4-5-full", description="Model ID")
