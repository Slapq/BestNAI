"""Legacy BestNAI estimation compatibility layer.

BestNAI previously estimated Anlas with the vendored SDK's own helpers while
pricing requests with a different formula, so a quote and the eventual charge
could disagree. This module now delegates to the single authoritative estimator in
`pricing.py`; the response *shape* is unchanged so existing clients keep working.

The numbers are estimates. They are not an official NovelAI charge.
"""
from __future__ import annotations

from typing import Any

from .errors import PlatformError
from .generation import extract_job_fields, normalize_native_payload
from .key_policy import canonical_model_id
from .model_alias import parse_model_alias
from .pricing import extract_pricing_features, estimate_anlas


def _resolve_alias(
    model: str | None,
    action: str | None,
    default_max_anlas: int,
) -> tuple[str, str, int]:
    """Apply BestNAI's `-anlas-N` / `-points-N` alias budget.

    Returns `(requested_model, resolved_model, max_anlas)`.
    """
    try:
        parsed = parse_model_alias(model, action, default_max_points=default_max_anlas)
    except ValueError as exc:
        raise PlatformError("INVALID_NATIVE_REQUEST", str(exc), 400) from exc
    return parsed.requested_model, parsed.resolved_model, parsed.max_points


def estimate_anlas_breakdown(
    payload: dict[str, Any],
    *,
    default_max_anlas: int = -1,
) -> dict[str, Any]:
    """Estimate Anlas for a payload, in the legacy BestNAI response shape.

    Accepts any supported dialect. The estimate is computed from the request's own
    dimensions, steps, and features, independent of which pricing rule would price
    it, because this endpoint reports Anlas rather than Gems.
    """
    native_payload = normalize_native_payload(payload)
    job_fields = extract_job_fields(native_payload)
    requested_model, resolved_model, max_anlas = _resolve_alias(
        native_payload.get("model"),
        native_payload.get("action"),
        default_max_anlas,
    )

    features = extract_pricing_features(native_payload)
    estimate = estimate_anlas(
        model=resolved_model,
        width=job_fields["width"],
        height=job_fields["height"],
        steps=job_fields["steps"],
        n_samples=job_fields["n_samples"],
        features=features,
    )

    total = int(estimate["estimated_anlas"])
    allowed = max_anlas < 0 or total <= max_anlas
    notes = ["unlimited_budget"] if max_anlas < 0 else []

    return {
        "requested_model": requested_model,
        "resolved_model": canonical_model_id(resolved_model),
        "max_anlas": max_anlas,
        "allowed": allowed,
        "is_opus_assumed": False,
        "estimated_anlas": {
            "model": requested_model,
            "requested_model": requested_model,
            "resolved_model": canonical_model_id(resolved_model),
            "size": (job_fields["width"], job_fields["height"]),
            "width": job_fields["width"],
            "height": job_fields["height"],
            "steps": job_fields["steps"],
            "n_samples": job_fields["n_samples"],
            "total": total,
            "base": int(estimate["base_anlas"]),
            "character_reference": int(estimate["character_reference_anlas"]),
            "vibe_encoding": int(estimate["vibe_encoding_anlas"]),
            "vibe_reference": int(estimate["vibe_reference_anlas"]),
            "per_image": int(estimate["per_image_anlas"]),
            "requested_samples": int(estimate["requested_samples"]),
            "billable_samples": int(estimate["billable_samples"]),
            "strength_factor": float(estimate["strength_factor"]),
            "is_opus": False,
            "opus_discount_applied": bool(estimate["opus_discount_applied"]),
            "allowed": allowed,
            "max_anlas": max_anlas,
            "limit_reason": None if allowed else "estimated_anlas_exceeds_limit",
            "notes": notes,
            "cached_vibe_count": int(features["cached_vibe_count"]),
            "uncached_vibe_count": int(features["uncached_vibe_count"]),
        },
        "note": (
            "Anlas values are estimates used for platform pricing; "
            "they are not an official NovelAI charge."
        ),
    }


def enforce_alias_limit(info: dict[str, Any]) -> None:
    """Reject a request whose estimate exceeds the model alias budget."""
    max_anlas = int(info.get("max_anlas") or -1)
    total = int((info.get("estimated_anlas") or {}).get("total") or 0)
    if max_anlas >= 0 and total > max_anlas:
        raise PlatformError(
            "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED",
            "Estimated Anlas exceeds the model alias limit",
            402,
            {
                "estimated_anlas": total,
                "max_anlas": max_anlas,
                "resolved_model": info.get("resolved_model"),
                "requested_model": info.get("requested_model"),
            },
        )


__all__ = ["enforce_alias_limit", "estimate_anlas_breakdown"]
