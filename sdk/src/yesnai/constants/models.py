"""Image model constants."""

from __future__ import annotations

from typing import Literal

ImageModel = Literal[
    "nai-diffusion-5-full",
    "nai-diffusion-5-curated",
    "nai-diffusion-5-full-inpainting",
    "nai-diffusion-5-curated-inpainting",
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-5-full-inpainting",
    "nai-diffusion-4-5-curated-inpainting",
    "nai-diffusion-4-full",
    "nai-diffusion-4-curated",
    "nai-diffusion-4-full-inpainting",
    "nai-diffusion-4-curated-inpainting",
    "nai-diffusion-3",
    "nai-diffusion-3-inpainting",
    "nai-diffusion-3-furry",
    "nai-diffusion-3-furry-inpainting",
]

V5_FULL: ImageModel = "nai-diffusion-5-full"
V5_CURATED: ImageModel = "nai-diffusion-5-curated"
V4_5_FULL: ImageModel = "nai-diffusion-4-5-full"
V4_5_CURATED: ImageModel = "nai-diffusion-4-5-curated"
V4_FULL: ImageModel = "nai-diffusion-4-full"
V4_CURATED: ImageModel = "nai-diffusion-4-curated"
V3: ImageModel = "nai-diffusion-3"
V3_FURRY: ImageModel = "nai-diffusion-3-furry"

SUPPORTED_MODELS: tuple[str, ...] = (
    "nai-diffusion-5-curated",
    "nai-diffusion-5-full",
    "nai-diffusion-5-curated-inpainting",
    "nai-diffusion-5-full-inpainting",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated-inpainting",
    "nai-diffusion-4-5-full-inpainting",
    "nai-diffusion-4-curated",
    "nai-diffusion-4-full",
    "nai-diffusion-4-curated-inpainting",
    "nai-diffusion-4-full-inpainting",
    "nai-diffusion-3",
    "nai-diffusion-3-inpainting",
    "nai-diffusion-3-furry",
    "nai-diffusion-3-furry-inpainting",
)

NAI5_MODELS: frozenset[str] = frozenset({
    "nai-diffusion-5-curated",
    "nai-diffusion-5-full",
    "nai-diffusion-5-curated-inpainting",
    "nai-diffusion-5-full-inpainting",
})

SMEA_MODELS: frozenset[str] = frozenset({
    "nai-diffusion-3",
    "nai-diffusion-3-inpainting",
    "nai-diffusion-3-furry",
    "nai-diffusion-3-furry-inpainting",
})

PRECISE_REFERENCE_MODELS: frozenset[str] = frozenset({
    "nai-diffusion-5-curated",
    "nai-diffusion-5-full",
    "nai-diffusion-5-curated-inpainting",
    "nai-diffusion-5-full-inpainting",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated-inpainting",
    "nai-diffusion-4-5-full-inpainting",
})

STRUCTURED_PROMPT_MODELS: frozenset[str] = frozenset(
    model for model in SUPPORTED_MODELS if "diffusion-4" in model or "diffusion-5" in model
)

#: Deprecated IDs the gateway still accepts.
LEGACY_MODEL_ALIASES: dict[str, str] = {
    "nai-diffusion-furry-3": "nai-diffusion-3-furry",
    "nai-diffusion-furry-3-inpainting": "nai-diffusion-3-furry-inpainting",
    "nai-diffusion-4-curated-preview": "nai-diffusion-4-curated",
}


def canonical_model_id(model: object) -> str:
    """Map a deprecated model ID onto its canonical replacement."""
    normalized = str(model or "").strip()
    return LEGACY_MODEL_ALIASES.get(normalized, normalized)


def is_nai5_model(model: object) -> bool:
    canonical = canonical_model_id(model)
    return canonical in NAI5_MODELS or "diffusion-5" in canonical.lower()
