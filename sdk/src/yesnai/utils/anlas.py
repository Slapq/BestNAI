"""Local NovelAI Anlas estimation.

This mirrors the gateway's estimator so a caller can predict a price before
sending a request.

**The value is an estimate.** It is derived from a reverse-engineered formula, not
from NovelAI's official accounting. It is suitable for pricing and for comparing
requests, and it must not be presented as an authoritative upstream charge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..constants import canonical_model_id, is_nai5_model

#: Anlas formula coefficients.
AREA_COEFFICIENT = 2.951823174884865e-6
AREA_STEP_COEFFICIENT = 5.753298233447344e-7

#: NAI5 requests cost 1.5x the base estimate.
NAI5_MULTIPLIER = 1.5

#: SMEA factors.
SMEA_FACTOR = 1.2
SMEA_DYN_FACTOR = 1.4

#: Per-image floor and ceiling.
MIN_PER_IMAGE_ANLAS = 2
MAX_PER_IMAGE_ANLAS = 140

#: Opus free-tier boundary for lower models.
OPUS_FREE_MAX_PIXELS = 1_048_576
OPUS_FREE_MAX_STEPS = 28

#: Precise Reference and Vibe costs.
CHARACTER_REFERENCE_ANLAS = 5
VIBE_ENCODING_ANLAS = 2
VIBE_REFERENCE_ANLAS = 2
FREE_VIBE_REFERENCES = 4

_V4_PLUS_MARKERS = ("diffusion-4", "diffusion-5")


def _is_v4_plus(model: str) -> bool:
    return any(marker in model for marker in _V4_PLUS_MARKERS)


@dataclass(slots=True)
class AnlasEstimate:
    """A breakdown of one estimated request cost."""

    estimated_anlas: int
    base_anlas: int
    per_image_anlas: int
    requested_samples: int
    billable_samples: int
    free_base_applied: bool
    free_base_samples: int
    free_base_anlas: int
    opus_discount_applied: bool
    strength_factor: float
    smea_factor: float
    character_reference_anlas: int
    vibe_encoding_anlas: int
    vibe_reference_anlas: int
    billable_vibe_references: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimated_anlas": self.estimated_anlas,
            "base_anlas": self.base_anlas,
            "per_image_anlas": self.per_image_anlas,
            "requested_samples": self.requested_samples,
            "billable_samples": self.billable_samples,
            "free_base_applied": self.free_base_applied,
            "free_base_samples": self.free_base_samples,
            "free_base_anlas": self.free_base_anlas,
            "opus_discount_applied": self.opus_discount_applied,
            "strength_factor": self.strength_factor,
            "smea_factor": self.smea_factor,
            "character_reference_anlas": self.character_reference_anlas,
            "vibe_encoding_anlas": self.vibe_encoding_anlas,
            "vibe_reference_anlas": self.vibe_reference_anlas,
            "billable_vibe_references": self.billable_vibe_references,
        }


def calculate_anlas(
    *,
    model: str,
    width: int,
    height: int,
    steps: int,
    n_samples: int = 1,
    character_reference_count: int = 0,
    uncached_vibe_count: int = 0,
    vibe_reference_count: int = 0,
    i2i_strength: float = 1.0,
    inpaint_strength: float = 1.0,
    is_i2i: bool = False,
    is_inpaint: bool = False,
    smea: bool = False,
    smea_dyn: bool = False,
    is_opus: bool = False,
    free_base_samples: int = 0,
) -> AnlasEstimate:
    """Estimate the Anlas cost of one request.

    `width`/`height`/`steps` must describe the request that will actually be sent.
    """
    canonical = canonical_model_id(model)
    area = width * height
    raw_cost = area * AREA_COEFFICIENT + area * steps * AREA_STEP_COEFFICIENT
    if is_nai5_model(canonical):
        base_cost = math.ceil(math.ceil(raw_cost) * NAI5_MULTIPLIER)
    else:
        base_cost = math.ceil(raw_cost)

    smea_factor = SMEA_DYN_FACTOR if smea_dyn else SMEA_FACTOR if smea else 1.0
    strength_factor = inpaint_strength if is_inpaint else i2i_strength if is_i2i else 1.0
    per_image_anlas = max(math.ceil(base_cost * smea_factor * strength_factor), MIN_PER_IMAGE_ANLAS)
    if per_image_anlas > MAX_PER_IMAGE_ANLAS:
        raise ValueError(
            f"Estimated per-image Anlas ({per_image_anlas}) exceeds the supported maximum "
            f"({MAX_PER_IMAGE_ANLAS})"
        )

    opus_eligible = bool(
        is_opus and not is_i2i and not is_inpaint and area <= OPUS_FREE_MAX_PIXELS and steps <= OPUS_FREE_MAX_STEPS
    )
    opus_discount_samples = 1 if opus_eligible else 0
    free_base_samples = min(max(int(free_base_samples), 0), n_samples)
    discounted_samples = min(n_samples, max(opus_discount_samples, free_base_samples))
    billable_samples = n_samples - discounted_samples

    base_anlas = per_image_anlas * billable_samples
    free_base_anlas = per_image_anlas * free_base_samples
    character_reference_anlas = character_reference_count * n_samples * CHARACTER_REFERENCE_ANLAS
    vibe_encoding_anlas = uncached_vibe_count * VIBE_ENCODING_ANLAS if _is_v4_plus(canonical) else 0
    billable_vibe_references = (
        max(vibe_reference_count - FREE_VIBE_REFERENCES, 0) if _is_v4_plus(canonical) else 0
    )
    vibe_reference_anlas = billable_vibe_references * VIBE_REFERENCE_ANLAS

    return AnlasEstimate(
        estimated_anlas=base_anlas + character_reference_anlas + vibe_encoding_anlas + vibe_reference_anlas,
        base_anlas=base_anlas,
        per_image_anlas=per_image_anlas,
        requested_samples=n_samples,
        billable_samples=billable_samples,
        free_base_applied=free_base_samples > 0,
        free_base_samples=free_base_samples,
        free_base_anlas=free_base_anlas,
        opus_discount_applied=opus_eligible,
        strength_factor=strength_factor,
        smea_factor=smea_factor,
        character_reference_anlas=character_reference_anlas,
        vibe_encoding_anlas=vibe_encoding_anlas,
        vibe_reference_anlas=vibe_reference_anlas,
        billable_vibe_references=billable_vibe_references,
    )
