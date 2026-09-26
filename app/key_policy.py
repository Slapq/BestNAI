"""Model vocabulary and small shared helpers.

BestNAI is a gateway: it schedules keys, prices requests, and forwards them to
NovelAI.  This module holds only the shared vocabulary that pricing, request
validation, and error mapping need.  It deliberately contains **no** key-routing
policy — BestNAI does not classify requests to choose keys, and it does not model
subscription tiers, free allowances, or key quarantine states.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Final

# ── Upstream error codes ──────────────────────────────────────────────────────

ERROR_CODE_INVALID_KEY: Final = "NAI_KEY_INVALID"
ERROR_CODE_INSUFFICIENT_CREDITS: Final = "NAI_INSUFFICIENT_CREDITS_OR_SUBSCRIPTION"


def upstream_error_code(status_code: int) -> str | None:
    """Map an upstream HTTP status onto a stable, client-visible error code."""
    if status_code == 401:
        return ERROR_CODE_INVALID_KEY
    if status_code == 402:
        return ERROR_CODE_INSUFFICIENT_CREDITS
    return None


# ── Models ────────────────────────────────────────────────────────────────────

LEGACY_MODEL_ALIASES: Final[dict[str, str]] = {
    "nai-diffusion-furry-3": "nai-diffusion-3-furry",
    "nai-diffusion-furry-3-inpainting": "nai-diffusion-3-furry-inpainting",
    "nai-diffusion-4-curated-preview": "nai-diffusion-4-curated",
}

SUPPORTED_NAI_MODELS: Final[tuple[str, ...]] = (
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

SUPPORTED_NAI_MODEL_SET: Final = frozenset(SUPPORTED_NAI_MODELS)

NAI5_MODELS: Final = frozenset({
    "nai-diffusion-5-curated",
    "nai-diffusion-5-full",
    "nai-diffusion-5-curated-inpainting",
    "nai-diffusion-5-full-inpainting",
})

SMEA_MODELS: Final = frozenset({
    "nai-diffusion-3",
    "nai-diffusion-3-inpainting",
    "nai-diffusion-3-furry",
    "nai-diffusion-3-furry-inpainting",
})

PRECISE_REFERENCE_MODELS: Final = frozenset({
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated-inpainting",
    "nai-diffusion-4-5-full-inpainting",
    "nai-diffusion-5-curated",
    "nai-diffusion-5-full",
    "nai-diffusion-5-curated-inpainting",
    "nai-diffusion-5-full-inpainting",
})

STRUCTURED_PROMPT_MODELS: Final = frozenset(
    model for model in SUPPORTED_NAI_MODEL_SET if "diffusion-4" in model or "diffusion-5" in model
)


def canonical_model_id(model: object) -> str:
    """Map deprecated NovelAI model IDs onto their canonical replacements."""
    normalized = str(model or "").strip()
    return LEGACY_MODEL_ALIASES.get(normalized, normalized)


def equivalent_nai_model_ids(model: object) -> list[str]:
    """Every identifier that should match a pricing rule written for `model`."""
    canonical = canonical_model_id(model)
    identifiers = [canonical]
    for legacy, replacement in LEGACY_MODEL_ALIASES.items():
        if replacement == canonical:
            identifiers.append(legacy)
    return identifiers


def is_nai5_model(model: object) -> bool:
    canonical = canonical_model_id(model)
    return canonical in NAI5_MODELS or "diffusion-5" in canonical.lower()


# ── Image bounds ──────────────────────────────────────────────────────────────

DEFAULT_MAX_IMAGE_DIMENSION: Final = 2048
DEFAULT_MAX_IMAGE_PIXELS: Final = 3_145_728
DEFAULT_MIN_IMAGE_DIMENSION: Final = 64
DEFAULT_IMAGE_DIMENSION_STEP: Final = 64


# ── Key identity helpers ──────────────────────────────────────────────────────


def key_hash(api_key: str) -> str:
    """Stable, non-reversible key identity used in logs and URLs."""
    return sha256(api_key.encode("utf-8")).hexdigest()


def mask_key(api_key: str) -> str:
    """Masked display form. Never returns a usable key."""
    if len(api_key) <= 13:
        return api_key[:4] + "***"
    return f"{api_key[:7]}...{api_key[-6:]}"
