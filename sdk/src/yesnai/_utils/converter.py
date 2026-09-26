"""Convert high-level user parameters into the gateway's native payload.

The gateway accepts both shapes, so the SDK converts locally to keep behaviour
predictable and to make the wire format visible to callers.  Low-level requests
are never converted — they are passed through as-is.
"""
from __future__ import annotations

from typing import Any

from ..constants import UC_PRESET_TO_INT, canonical_model_id
from ..types.user.image import GenerateImageParams, image_to_base64


def convert_user_params_to_native(
    params: GenerateImageParams,
    *,
    stream: bool = False,
) -> dict[str, Any]:
    """Build a native payload from high-level parameters.

    Quality tags and the UC preset are applied here, matching the gateway's own
    high-level handling.
    """
    width, height = params.resolved_size()

    parameters: dict[str, Any] = {
        "width": width,
        "height": height,
        "steps": params.steps,
        "n_samples": params.n_samples,
        "sampler": params.sampler,
        "scale": params.scale,
        "seed": params.seed,
        "qualityToggle": params.quality,
        "uc_preset": params.uc_preset,
        "noise_schedule": params.noise_schedule,
        "cfg_rescale": params.cfg_rescale,
    }

    negative_prompt = params.effective_negative_prompt()
    if negative_prompt:
        parameters["negative_prompt"] = negative_prompt

    if params.variety_boost:
        # Variety Boost is expressed as a CFG skip threshold.
        parameters["skip_cfg_above_sigma"] = 58.0

    if params.i2i is not None:
        parameters["i2i"] = {
            "image": image_to_base64(params.i2i.image),
            "strength": params.i2i.strength,
        }

    if params.inpaint is not None:
        parameters["inpaint"] = {
            "image": image_to_base64(params.inpaint.image),
            "mask": image_to_base64(params.inpaint.mask),
            "strength": params.inpaint.strength,
        }

    if params.controlnet is not None:
        parameters["controlnet"] = {
            "images": [
                {
                    "image": image_to_base64(entry.image),
                    "info_extracted": entry.info_extracted,
                    "strength": entry.strength,
                }
                for entry in params.controlnet.images
            ],
            "strength": params.controlnet.strength,
        }

    if params.character_references:
        # The gateway's high-level path expects the object form, which carries
        # the reference type and fidelity alongside the image.
        parameters["character_references"] = [
            {
                "image": image_to_base64(entry.image),
                "type": entry.type,
                "fidelity": entry.fidelity,
                "strength": entry.strength,
            }
            for entry in params.character_references
        ]

    if params.characters:
        parameters["characters"] = [
            {
                "prompt": character.prompt,
                "negative_prompt": character.negative_prompt,
                "position": list(character.position),
                "enabled": character.enabled,
            }
            for character in params.characters
        ]

    if stream:
        parameters["stream"] = "msgpack"

    return {
        "model": canonical_model_id(params.model),
        "action": "generate",
        "input": params.effective_prompt(),
        "parameters": parameters,
    }


def convert_request_to_native(request: Any) -> dict[str, Any]:
    """Serialize a low-level SDK request model without adding or dropping fields."""
    return request.model_dump(mode="json", exclude_none=True)


def uc_preset_to_int(preset: str) -> int:
    """Convert a UC preset name to the low-level integer form."""
    return UC_PRESET_TO_INT.get(preset, 1)
