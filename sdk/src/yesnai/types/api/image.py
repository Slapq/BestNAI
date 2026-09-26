"""Low-level API type definitions.

These mirror the NovelAI REST image API exactly, which is also the shape the
gateway accepts at `/native/*` and `/ai/*`.  No automatic processing (quality
tags, UC presets, V4 prompt structures) is performed here.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CenterPoint(BaseModel):
    """Character center position in object form."""

    x: float = Field(..., description="X coordinate")
    y: float = Field(..., description="Y coordinate")


class CharacterPrompt(BaseModel):
    """Character-specific prompt with positioning."""

    prompt: str = Field(..., description="Character prompt")
    uc: str = Field(default="", description="Character undesired content")
    center: CenterPoint = Field(..., description="Character center position")
    enabled: bool = Field(default=True, description="Enable this character prompt")


class V4CharacterCaption(BaseModel):
    """Character caption with positioning for V4/V4.5 prompts."""

    centers: list[CenterPoint] | None = Field(default=None, description="Character centers")
    char_caption: str | None = Field(default=None, description="Character-specific caption")


class V4ConditionInput(BaseModel):
    """Structured V4/V4.5 prompt input."""

    caption: dict[str, Any] = Field(..., description="Base caption payload")
    use_coords: bool = Field(default=False, description="Use explicit coordinates")
    use_order: bool = Field(default=True, description="Respect character order")
    legacy_uc: bool = Field(default=False, description="Use legacy undesired content")
    character_captions: list[V4CharacterCaption] | None = Field(
        default=None, description="Per-character captions"
    )


class Img2ImgParams(BaseModel):
    """Nested img2img parameters."""

    image: str = Field(..., description="Base64 encoded source image")
    strength: float | None = Field(default=None, ge=0.0, le=1.0, description="img2img strength")


class ImageParameters(BaseModel):
    """Complete low-level image parameter model.

    `extra="forbid"` is intentional: an unknown field is a caller mistake and
    must surface instead of being silently dropped upstream.
    """

    model_config = ConfigDict(extra="forbid")

    # Basic
    width: int | None = Field(default=None, ge=64, le=2048, description="Image width")
    height: int | None = Field(default=None, ge=64, le=2048, description="Image height")
    steps: int | None = Field(default=None, ge=1, le=50, description="Sampling steps")
    scale: float | None = Field(default=None, ge=0.0, le=10.0, description="Prompt guidance")
    sampler: str | None = Field(default=None, description="Sampling method")
    seed: int | None = Field(default=None, description="Random seed")
    n_samples: int | None = Field(default=None, ge=1, le=8, description="Images to generate")

    # Prompt
    prompt: str | None = Field(default=None, description="Positive prompt")
    negative_prompt: str | None = Field(default=None, description="Negative prompt")
    ucPreset: int | None = Field(default=None, ge=0, le=3, description="Undesired content preset")
    qualityToggle: bool | None = Field(default=None, description="Enable quality tags")

    # V4/V4.5 structured prompts
    v4_prompt: V4ConditionInput | None = Field(default=None, description="V4 prompt structure")
    v4_negative_prompt: V4ConditionInput | None = Field(
        default=None, description="V4 negative prompt structure"
    )

    # Sampling
    sm: bool | None = Field(default=None, description="SMEA sampling (V3 only)")
    sm_dyn: bool | None = Field(default=None, description="SMEA DYN sampling (V3 only)")
    autoSmea: bool | None = Field(default=None, description="Automatic SMEA")
    dynamic_thresholding: bool | None = Field(default=None, description="Dynamic thresholding")
    cfg_rescale: float | None = Field(default=None, ge=0.0, le=1.0, description="CFG rescale")
    noise_schedule: str | None = Field(default=None, description="Noise schedule")
    legacy: bool | None = Field(default=None, description="Legacy mode")
    legacy_uc: bool | None = Field(default=None, description="Legacy undesired content")
    legacy_v3_extend: bool | None = Field(default=None, description="Legacy V3 extend")
    skip_cfg_above_sigma: float | None = Field(default=None, description="Variety Boost threshold")
    deliberate_euler_ancestral_bug: bool | None = Field(default=None, description="Euler bug compat")
    prefer_brownian: bool | None = Field(default=None, description="Prefer Brownian noise")

    # img2img
    image: str | None = Field(default=None, description="Base64 encoded source image")
    strength: float | None = Field(default=None, ge=0.0, le=1.0, description="img2img strength")
    noise: float | None = Field(default=None, description="Noise amount")
    extra_noise_seed: int | None = Field(default=None, description="Extra noise seed")
    img2img: Img2ImgParams | None = Field(default=None, description="Nested img2img params")

    # Inpainting
    mask: str | None = Field(default=None, description="Base64 encoded mask")
    add_original_image: bool | None = Field(default=None, description="Include original image")
    color_correct: bool | None = Field(default=None, description="Apply color correction")
    inpaintImg2ImgStrength: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Inpaint strength"
    )

    # ControlNet / Vibe
    controlnet_strength: float | None = Field(default=None, ge=0.0, le=1.0, description="ControlNet strength")
    normalize_reference_strength_multiple: bool | None = Field(
        default=None, description="Normalize reference strengths"
    )
    reference_information_extracted: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Reference information extracted"
    )
    reference_image_multiple: list[str] | None = Field(
        default=None, description="Encoded vibe-transfer data"
    )
    reference_strength_multiple: list[float] | None = Field(default=None, description="Reference strengths")
    reference_information_extracted_multiple: list[float] | None = Field(
        default=None, description="Reference information extracted values"
    )

    # Director tools (Precise Reference)
    director_reference_images: list[str] | None = Field(default=None, description="Reference images")
    director_reference_descriptions: list[dict[str, Any]] | None = Field(
        default=None, description="Reference descriptions"
    )
    director_reference_information_extracted: list[float] | None = Field(
        default=None, description="Reference extraction strengths"
    )
    director_reference_secondary_strength_values: list[float] | None = Field(
        default=None, description="Reference secondary strengths"
    )

    # Character prompts
    characterPrompts: list[CharacterPrompt] | None = Field(
        default=None, description="Character prompts with positions"
    )

    # Stream control
    stream: str | None = Field(default=None, description="Streaming type (sse)")


class ImageStreamParameters(ImageParameters):
    """Low-level parameters for a streaming request."""


class BaseImageGenerationRequest(BaseModel):
    """Base low-level generation request."""

    model_config = ConfigDict(extra="forbid")

    action: str | None = Field(default=None, description="Action (generate, img2img, infill...)")
    input: str = Field(..., min_length=1, description="Text prompt")
    model: str | None = Field(default=None, description="Model ID")
    use_new_shared_trial: bool | None = Field(default=None, description="Use shared trial system")
    url: str | None = Field(default=None, description="Optional URL field")


class ImageGenerationRequest(BaseImageGenerationRequest):
    """Complete non-streaming request."""

    parameters: ImageParameters = Field(..., description="Generation parameters")


class StreamImageGenerationRequest(BaseImageGenerationRequest):
    """Complete streaming request."""

    parameters: ImageStreamParameters = Field(..., description="Streaming parameters")


class EncodeVibeRequest(BaseModel):
    """Parameters for vibe encoding."""

    image: str = Field(..., description="Base64 image data")
    information_extracted: float = Field(..., gt=0, le=1, description="Extraction strength")
    model: str = Field(..., description="Model name")


class SuggestTagsResponse(BaseModel):
    """Tag suggestion response."""

    tags: list[str] = Field(default_factory=list, description="Suggested tags")


class ImageStreamChunk(BaseModel):
    """One decoded streaming frame."""

    model_config = ConfigDict(extra="allow")

    event_type: Literal["intermediate", "final", "error"] | str = Field(
        ..., description="Frame type"
    )
    samp_ix: int | None = Field(default=None, description="Sample index")
    step_ix: int | None = Field(default=None, description="Step index")
    gen_id: int | None = Field(default=None, description="Generation ID")
    sigma: float | None = Field(default=None, description="Current sigma")
    image: str | None = Field(default=None, description="Base64 image for this frame")
    message: str | None = Field(default=None, description="Error message, when present")
    code: str | None = Field(default=None, description="Error code, when present")
    raw: dict[str, Any] = Field(default_factory=dict, description="Raw decoded frame")

    @classmethod
    def from_frame(cls, frame: dict[str, Any]) -> "ImageStreamChunk":
        """Build a chunk from a decoded MessagePack/JSON frame."""
        image = frame.get("image")
        if image is None:
            image = frame.get("data")
        return cls(
            event_type=str(frame.get("event_type") or frame.get("event") or "unknown"),
            samp_ix=frame.get("samp_ix"),
            step_ix=frame.get("step_ix"),
            gen_id=frame.get("gen_id"),
            sigma=frame.get("sigma"),
            image=image,
            message=frame.get("message"),
            code=frame.get("code"),
            raw=frame,
        )
