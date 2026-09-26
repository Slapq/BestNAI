"""Pricing engine: pricing rules, feature extraction, and the two formulas.

Two calculation families are supported, matching YesNovelAI's contract:

* ``gems_flat`` — a configured per-image Gems base multiplied by action, strength,
  and SMEA factors, plus per-feature extras (character reference, vibe).
* ``anlas_formula`` — the "System Anlas Fallback": a reverse-engineered Anlas
  estimate converted to Gems by a ratio.

A free package is not a separate formula; it is ``gems_flat`` with a free
admission identity.  It may still charge Gems while using the free routing
class.

The Anlas estimate is an *estimate*.  It is not an official NovelAI charge and
must not be presented as a billing receipt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .errors import PlatformError
from .key_policy import (
    NAI5_MODELS,
    SUPPORTED_NAI_MODEL_SET,
    canonical_model_id,
)

# ── Rule model ────────────────────────────────────────────────────────────────

PRICING_MODE_GEMS_FLAT = "gems_flat"
PRICING_MODE_ANLAS_FORMULA = "anlas_formula"
PRICING_MODE_OFFICIAL_REQUEST_PRICE = "official_request_price"

PRICING_MODES = frozenset({
    PRICING_MODE_GEMS_FLAT,
    PRICING_MODE_ANLAS_FORMULA,
    PRICING_MODE_OFFICIAL_REQUEST_PRICE,
})

PRICING_TYPE_FREE_PACKAGE = "free_package"
PRICING_TYPE_FLAT_GEMS = "flat_gems"
PRICING_TYPE_ANLAS_FORMULA = "anlas_formula"

FREE_PACKAGE_BUCKETS = frozenset({"free-standard", "free-package"})
SYSTEM_ANLAS_BUCKET = "system-anlas-fallback"

GENERATION_PRICING_ACTIONS = frozenset({"generate", "img2img", "inpaint"})

#: Fields that must not be used as a feature source; callers must use canonical names.
NONCANONICAL_FEATURE_FIELDS = frozenset({
    "controlnet",
    "vibe_images",
    "vibes",
    "cached_vibe_count",
    "uncached_vibe_count",
})

_V4_PLUS_MODELS = frozenset(
    {"custom"}
    | {
        model
        for model in SUPPORTED_NAI_MODEL_SET
        if "diffusion-4" in model or "diffusion-5" in model
    }
)


@dataclass(slots=True)
class PricingRule:
    """One pricing rule as stored."""

    id: int
    model: str
    action: str = "generate"
    size_bucket: str = "default"
    min_pixels: int = 0
    max_pixels: int = 3_145_728
    min_steps: int = 1
    max_steps: int = 50
    min_images: int = 1
    max_images: int = 1
    base_gems: int = 0
    enabled: bool = True
    priority: int = 100
    allowed_sizes: list[str] = field(default_factory=list)
    feature_config: dict[str, Any] = field(default_factory=dict)
    built_in: bool = False

    @property
    def pricing_mode(self) -> str:
        return pricing_mode(self.feature_config)

    @property
    def pricing_type(self) -> str:
        if is_free_package_rule(self):
            return PRICING_TYPE_FREE_PACKAGE
        if self.pricing_mode == PRICING_MODE_ANLAS_FORMULA:
            return PRICING_TYPE_ANLAS_FORMULA
        return PRICING_TYPE_FLAT_GEMS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "action": self.action,
            "size_bucket": self.size_bucket,
            "min_pixels": self.min_pixels,
            "max_pixels": self.max_pixels,
            "min_steps": self.min_steps,
            "max_steps": self.max_steps,
            "min_images": self.min_images,
            "max_images": self.max_images,
            "base_gems": self.base_gems,
            "enabled": self.enabled,
            "priority": self.priority,
            "allowed_sizes": list(self.allowed_sizes),
            "feature_config": dict(self.feature_config),
            "built_in": self.built_in,
            "pricing_type": self.pricing_type,
            "pricing_mode": self.pricing_mode,
        }


def rule_from_row(row: dict[str, Any], *, parse_json_object, parse_json_list) -> PricingRule:
    """Build a `PricingRule` from a stored row."""
    return PricingRule(
        id=int(row.get("id") or 0),
        model=str(row.get("model") or ""),
        action=str(row.get("action") or "generate"),
        size_bucket=str(row.get("size_bucket") or "default"),
        min_pixels=int(row.get("min_pixels") or 0),
        max_pixels=int(row.get("max_pixels") or 0),
        min_steps=int(row.get("min_steps") or 1),
        max_steps=int(row.get("max_steps") or 50),
        min_images=int(row.get("min_images") or 1),
        max_images=int(row.get("max_images") or 1),
        base_gems=int(row.get("base_gems") or 0),
        enabled=bool(row.get("enabled")),
        priority=int(row.get("priority") or 100),
        allowed_sizes=[str(item) for item in parse_json_list(row.get("allowed_sizes"))],
        feature_config=parse_json_object(row.get("feature_config")),
        built_in=bool(row.get("built_in")),
    )


# ── Rule classification ───────────────────────────────────────────────────────


def pricing_mode(config: dict[str, Any]) -> str:
    mode = str(config.get("pricing_mode") or PRICING_MODE_GEMS_FLAT).strip().lower()
    return mode if mode in PRICING_MODES else PRICING_MODE_GEMS_FLAT


def is_system_managed_anlas_rule(rule: PricingRule) -> bool:
    return bool(rule.feature_config.get("system_managed_anlas")) or rule.size_bucket == SYSTEM_ANLAS_BUCKET


def is_built_in_free_policy_rule(rule: PricingRule) -> bool:
    return rule.size_bucket == "free-standard" and bool(rule.feature_config.get("seeded_free_rule"))


def is_free_package_rule(rule: PricingRule) -> bool:
    """Return True when the rule defines a free-standard/free-package tier."""
    if is_built_in_free_policy_rule(rule):
        return True
    if rule.size_bucket in FREE_PACKAGE_BUCKETS:
        return True
    config = rule.feature_config
    return config.get("is_free_package") is True or config.get("seeded_free_rule") is True


def free_package_rule_config(rule: PricingRule) -> dict[str, int]:
    """Extract free quotas, windows, and pack multipliers for admission tracking."""
    config = rule.feature_config
    try:
        free_count = int(config.get("free_count", 5))
        free_window = int(config.get("free_window_minutes", 60))
        pack_mult = int(config.get("image_pack_multiplier", 1))
    except (TypeError, ValueError):
        free_count, free_window, pack_mult = 5, 60, 1
    return {
        "free_count": max(0, free_count),
        "free_window_minutes": max(1, free_window),
        "image_pack_multiplier": max(1, pack_mult),
    }


def anlas_to_gems_ratio(config: dict[str, Any]) -> float:
    try:
        ratio = float(config.get("anlas_to_gems_ratio", 1.0))
    except (TypeError, ValueError) as exc:
        raise PlatformError("BILLING_CONFIGURATION_INVALID", None, 503) from exc
    if not math.isfinite(ratio) or ratio <= 0:
        raise PlatformError("BILLING_CONFIGURATION_INVALID", None, 503)
    return ratio


def _config_number(config: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= 0 else default


def config_bool(config: dict[str, Any], key: str, default: bool = True) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


# ── Size matching ─────────────────────────────────────────────────────────────


def _parse_size_token(token: str) -> tuple[int, int] | None:
    for separator in ("×", "x", "X", "*"):
        if separator in token:
            left, _, right = token.partition(separator)
            try:
                return int(left.strip()), int(right.strip())
            except ValueError:
                return None
    return None


def allowed_sizes_match(rule: PricingRule, width: int, height: int) -> bool:
    """Return True when the request size is permitted by the rule's allowlist."""
    if not rule.allowed_sizes:
        return True
    for token in rule.allowed_sizes:
        parsed = _parse_size_token(str(token))
        if parsed is None:
            continue
        if parsed == (width, height) or parsed == (height, width):
            return True
    return False


# ── Feature extraction ────────────────────────────────────────────────────────


def _feature_count(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _payload_strength(
    params: dict[str, Any],
    key: str,
    *,
    nested_key: str | None = None,
) -> float:
    nested = params.get(nested_key) if nested_key is not None else None
    raw = nested.get("strength", 1.0) if isinstance(nested, dict) else params.get(key, 1.0)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise PlatformError("INVALID_PRICING_PARAMETER", f"INVALID_PRICING_PARAMETER:{key}", 400) from exc
    if not math.isfinite(value) or value < 0 or value > 1:
        raise PlatformError("INVALID_PRICING_PARAMETER", f"INVALID_PRICING_PARAMETER:{key}", 400)
    return value


def extract_pricing_features(native_payload: dict[str, Any]) -> dict[str, Any]:
    """Derive billable features from a native payload.

    Only canonical field names are honored.  `validate_generation_capabilities`
    rejects non-canonical aliases before pricing so this stays unambiguous.
    """
    params = native_payload.get("parameters")
    params = params if isinstance(params, dict) else {}
    action = str(native_payload.get("action") or "generate").lower()
    i2i = params.get("i2i")
    inpaint = params.get("inpaint")
    has_mask = params.get("mask") is not None or (
        isinstance(inpaint, dict) and inpaint.get("mask") is not None
    )
    has_image = params.get("image") is not None or (
        isinstance(i2i, dict) and i2i.get("image") is not None
    )
    is_inpaint = action in {"infill", "inpaint"} or has_mask or isinstance(inpaint, dict)
    is_i2i = not is_inpaint and (action == "img2img" or has_image or isinstance(i2i, dict))

    i2i_strength = _payload_strength(params, "strength", nested_key="i2i") if is_i2i else 1.0
    inpaint_strength = (
        _payload_strength(params, "inpaintImg2ImgStrength", nested_key="inpaint")
        if is_inpaint
        else 1.0
    )

    character_reference_count = max(
        _feature_count(params.get("director_reference_images")),
        _feature_count(params.get("character_references")),
        _feature_count(native_payload.get("character_references")),
    )
    encoded_vibes = _feature_count(params.get("reference_image_multiple"))
    controlnet = params.get("controlnet")
    controlnet_images = _feature_count(controlnet.get("images")) if isinstance(controlnet, dict) else 0
    raw_vibes = max(
        controlnet_images,
        _feature_count(params.get("vibe_images")),
        _feature_count(params.get("vibes")),
    )
    try:
        explicit_cached = int(params.get("cached_vibe_count") or 0)
    except (TypeError, ValueError):
        explicit_cached = 0
    try:
        explicit_uncached = int(params.get("uncached_vibe_count") or 0)
    except (TypeError, ValueError):
        explicit_uncached = 0
    cached_vibe_count = max(encoded_vibes, explicit_cached)
    uncached_vibe_count = max(raw_vibes, explicit_uncached)
    vibe_reference_count = max(cached_vibe_count + uncached_vibe_count, encoded_vibes, raw_vibes)

    return {
        "i2i": is_i2i,
        "inpaint": is_inpaint,
        "i2i_strength": i2i_strength,
        "inpaint_strength": inpaint_strength,
        "character_reference_count": character_reference_count,
        "cached_vibe_count": cached_vibe_count,
        "uncached_vibe_count": uncached_vibe_count,
        "vibe_reference_count": vibe_reference_count,
        "smea": bool(params.get("sm")),
        "smea_dyn": bool(params.get("sm") and params.get("sm_dyn")),
    }


def require_allowed_features(
    config: dict[str, Any],
    features: dict[str, Any],
    *,
    base_gems: int,
) -> None:
    """Reject a request whose features the matched rule does not permit."""
    gated_features = (
        (bool(features["inpaint"]), "allow_inpaint", "INPAINT"),
        (bool(features["i2i"]), "allow_i2i", "I2I"),
        (bool(features["smea_dyn"]), "allow_smea_dyn", "SMEA_DYN"),
        (bool(features["smea"]) and not bool(features["smea_dyn"]), "allow_smea", "SMEA"),
        (
            int(features["character_reference_count"]) > 0,
            "allow_character_reference",
            "CHARACTER_REFERENCE",
        ),
        (int(features["vibe_reference_count"]) > 0, "allow_vibe", "VIBE"),
    )
    for used, config_key, feature_code in gated_features:
        if not used:
            continue
        if base_gems <= 0:
            raise PlatformError(
                "PRICING_FEATURE_REQUIRES_PAID_BASE",
                f"PRICING_FEATURE_REQUIRES_PAID_BASE:{feature_code}",
                400,
            )
        if not config_bool(config, config_key):
            raise PlatformError(
                "PRICING_FEATURE_NOT_ALLOWED",
                f"PRICING_FEATURE_NOT_ALLOWED:{feature_code}",
                400,
            )


# ── Formula A: Anlas estimate → Gems ──────────────────────────────────────────


def estimate_anlas(
    *,
    model: str,
    width: int,
    height: int,
    steps: int,
    n_samples: int,
    features: dict[str, Any],
    is_opus: bool = False,
    free_base_samples: int = 0,
) -> dict[str, Any]:
    """Estimate NovelAI Anlas for one generation request.

    This is a reverse-engineered estimate used for pricing and routing only.
    It is not an authoritative upstream charge.
    """
    canonical = canonical_model_id(model)
    if canonical not in SUPPORTED_NAI_MODEL_SET and canonical != "custom":
        raise PlatformError("INVALID_PRICING_PARAMETER", None, 400)

    area = width * height
    raw_cost = area * 2.951823174884865e-6 + area * steps * 5.753298233447344e-7
    if canonical in NAI5_MODELS or "diffusion-5" in canonical.lower():
        base_cost = math.ceil(math.ceil(raw_cost) * 1.5)
    else:
        base_cost = math.ceil(raw_cost)

    smea_factor = 1.4 if features["smea_dyn"] else 1.2 if features["smea"] else 1.0
    strength_factor = (
        float(features["inpaint_strength"])
        if features["inpaint"]
        else float(features["i2i_strength"])
        if features["i2i"]
        else 1.0
    )
    per_image_anlas = max(math.ceil(base_cost * smea_factor * strength_factor), 2)
    if per_image_anlas > 140:
        raise PlatformError("INVALID_PRICING_PARAMETER", None, 400)

    opus_eligible = bool(
        is_opus and not features["i2i"] and not features["inpaint"] and area <= 1_048_576 and steps <= 28
    )
    opus_discount_samples = 1 if opus_eligible else 0
    free_base_samples = min(max(int(free_base_samples), 0), n_samples)
    discounted_samples = min(n_samples, max(opus_discount_samples, free_base_samples))
    billable_samples = n_samples - discounted_samples
    base_anlas = per_image_anlas * billable_samples
    free_base_anlas = per_image_anlas * free_base_samples
    character_reference_anlas = int(features["character_reference_count"]) * n_samples * 5
    vibe_encoding_anlas = int(features["uncached_vibe_count"]) * 2 if canonical in _V4_PLUS_MODELS else 0
    billable_vibe_references = (
        max(int(features["vibe_reference_count"]) - 4, 0) if canonical in _V4_PLUS_MODELS else 0
    )
    vibe_reference_anlas = billable_vibe_references * 2
    total_anlas = (
        base_anlas + character_reference_anlas + vibe_encoding_anlas + vibe_reference_anlas
    )
    return {
        "estimated_anlas": total_anlas,
        "base_anlas": base_anlas,
        "per_image_anlas": per_image_anlas,
        "requested_samples": n_samples,
        "billable_samples": billable_samples,
        "free_base_applied": free_base_samples > 0,
        "free_base_samples": free_base_samples,
        "free_base_anlas": free_base_anlas,
        "opus_discount_applied": opus_eligible,
        "strength_factor": strength_factor,
        "smea_factor": smea_factor,
        "character_reference_anlas": character_reference_anlas,
        "vibe_encoding_anlas": vibe_encoding_anlas,
        "vibe_reference_anlas": vibe_reference_anlas,
        "billable_vibe_references": billable_vibe_references,
    }


# ── Cost breakdown ────────────────────────────────────────────────────────────


def calculate_generation_cost_breakdown(
    rule: PricingRule,
    n_samples: int,
    native_payload: dict[str, Any] | None = None,
    *,
    model: str | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    is_opus: bool = False,
    force_anlas_ratio: float | None = None,
    free_base_samples: int = 0,
) -> dict[str, Any]:
    """Compute the full Gems breakdown for one request against one rule."""
    config = rule.feature_config
    features = extract_pricing_features(native_payload or {})
    mode = PRICING_MODE_ANLAS_FORMULA if force_anlas_ratio is not None else pricing_mode(config)
    is_free_package = mode == PRICING_MODE_GEMS_FLAT and is_free_package_rule(rule)

    if mode == PRICING_MODE_ANLAS_FORMULA:
        require_allowed_features(config, features, base_gems=1)
        params = (native_payload or {}).get("parameters")
        params = params if isinstance(params, dict) else {}
        resolved_model = str(model or (native_payload or {}).get("model") or "")
        resolved_width = int(width or params.get("width") or 0)
        resolved_height = int(height or params.get("height") or 0)
        resolved_steps = int(steps or params.get("steps") or 0)
        if not resolved_model or resolved_width <= 0 or resolved_height <= 0 or resolved_steps <= 0:
            raise PlatformError("INVALID_PRICING_PARAMETER", None, 400)
        estimate = estimate_anlas(
            model=resolved_model,
            width=resolved_width,
            height=resolved_height,
            steps=resolved_steps,
            n_samples=n_samples,
            features=features,
            is_opus=is_opus,
            free_base_samples=free_base_samples,
        )
        ratio = force_anlas_ratio if force_anlas_ratio is not None else anlas_to_gems_ratio(config)
        return {
            "pricing_mode": mode,
            "is_free_package": is_free_package,
            "anlas_to_gems_ratio": ratio,
            "anlas_price_multiplier": ratio,
            "total_gems": math.ceil(int(estimate["estimated_anlas"]) * ratio),
            "base_gems": math.ceil(int(estimate["base_anlas"]) * ratio),
            "free_base_gems": math.ceil(int(estimate["free_base_anlas"]) * ratio),
            "rule_base_gems": rule.base_gems,
            "sample_base_gems": 0,
            "per_image_base_gems": math.ceil(int(estimate["per_image_anlas"]) * ratio),
            "applied_action_strength": estimate["strength_factor"],
            "action_pricing_mode": PRICING_MODE_ANLAS_FORMULA,
            "matched_rule_action": rule.action,
            "applied_i2i_multiplier": 1.0,
            "applied_inpaint_multiplier": 1.0,
            "applied_smea_multiplier": estimate["smea_factor"],
            "applied_smea_dyn_multiplier": estimate["smea_factor"],
            "character_reference_gems": math.ceil(int(estimate["character_reference_anlas"]) * ratio),
            "vibe_encoding_gems": math.ceil(int(estimate["vibe_encoding_anlas"]) * ratio),
            "vibe_reference_gems": math.ceil(int(estimate["vibe_reference_anlas"]) * ratio),
            "features": features,
            **estimate,
        }

    if mode == PRICING_MODE_OFFICIAL_REQUEST_PRICE:
        # Dormant: no rule creation path enables this mode.
        raise PlatformError("PRICE_NOT_CONFIGURED", None, 400)

    require_allowed_features(config, features, base_gems=rule.base_gems)
    per_image_base = float(rule.base_gems)
    action_pricing_mode = "dedicated" if rule.action in {"img2img", "inpaint"} else "multiplier"
    applied_i2i_multiplier = 1.0
    applied_inpaint_multiplier = 1.0
    if features["inpaint"] and action_pricing_mode == "multiplier":
        applied_inpaint_multiplier = _config_number(config, "inpaint_multiplier", 1.0)
        per_image_base *= applied_inpaint_multiplier
    elif features["i2i"] and action_pricing_mode == "multiplier":
        applied_i2i_multiplier = _config_number(config, "i2i_multiplier", 1.0)
        per_image_base *= applied_i2i_multiplier
    applied_action_strength = (
        float(features["inpaint_strength"])
        if features["inpaint"]
        else float(features["i2i_strength"])
        if features["i2i"]
        else 1.0
    )
    per_image_base *= applied_action_strength
    applied_smea_multiplier = 1.0
    applied_smea_dyn_multiplier = 1.0
    if features["smea_dyn"]:
        applied_smea_dyn_multiplier = _config_number(config, "smea_dyn_multiplier", 1.0)
        per_image_base *= applied_smea_dyn_multiplier
    elif features["smea"]:
        applied_smea_multiplier = _config_number(config, "smea_multiplier", 1.0)
        per_image_base *= applied_smea_multiplier

    per_image_gems = math.ceil(per_image_base)
    if rule.base_gems > 0:
        per_image_gems = max(per_image_gems, 1)
    base_gems = per_image_gems * n_samples

    character_reference_gems = (
        int(features["character_reference_count"])
        * n_samples
        * math.ceil(_config_number(config, "character_reference_gems", 0.0))
    )
    vibe_encoding_gems = int(features["uncached_vibe_count"]) * math.ceil(
        _config_number(config, "vibe_encoding_gems", 0.0)
    )
    free_references = int(_config_number(config, "vibe_free_references", 4.0))
    billable_vibe_references = max(int(features["vibe_reference_count"]) - free_references, 0)
    vibe_reference_gems = billable_vibe_references * math.ceil(
        _config_number(config, "vibe_reference_gems", 0.0)
    )
    total = base_gems + character_reference_gems + vibe_encoding_gems + vibe_reference_gems

    return {
        "pricing_mode": mode,
        "is_free_package": is_free_package,
        "anlas_to_gems_ratio": 1.0,
        "anlas_price_multiplier": 1.0,
        "estimated_anlas": None,
        "opus_discount_applied": False,
        "total_gems": total,
        "base_gems": base_gems,
        "rule_base_gems": rule.base_gems,
        "sample_base_gems": rule.base_gems * n_samples,
        "per_image_base_gems": per_image_gems,
        "applied_action_strength": applied_action_strength,
        "action_pricing_mode": action_pricing_mode,
        "matched_rule_action": rule.action,
        "applied_i2i_multiplier": applied_i2i_multiplier,
        "applied_inpaint_multiplier": applied_inpaint_multiplier,
        "applied_smea_multiplier": applied_smea_multiplier,
        "applied_smea_dyn_multiplier": applied_smea_dyn_multiplier,
        "character_reference_gems": character_reference_gems,
        "vibe_encoding_gems": vibe_encoding_gems,
        "vibe_reference_gems": vibe_reference_gems,
        "billable_vibe_references": billable_vibe_references,
        "features": features,
    }


def calculate_generation_cost(
    rule: PricingRule,
    n_samples: int,
    native_payload: dict[str, Any] | None = None,
) -> int:
    return int(calculate_generation_cost_breakdown(rule, n_samples, native_payload)["total_gems"])


# ── Rule matching (pure, over a supplied rule list) ───────────────────────────


def _model_rank(rule: PricingRule, model: str, equivalents: list[str]) -> int:
    if rule.model == canonical_model_id(model):
        return 0
    if rule.model in equivalents:
        return 1
    return 2


def _size_range_matches(
    rule: PricingRule,
    *,
    pixels: int,
    steps: int,
    n_samples: int,
) -> bool:
    return (
        rule.min_pixels <= pixels <= rule.max_pixels
        and rule.min_steps <= steps <= rule.max_steps
        and rule.min_images <= n_samples <= rule.max_images
    )


def _sort_key(rule: PricingRule, model: str, action: str, equivalents: list[str]) -> tuple[int, int, int, int]:
    return (
        _model_rank(rule, model, equivalents),
        0 if rule.action == action else 1,
        rule.priority,
        -rule.id,
    )


def match_free_package_rule(
    rules: list[PricingRule],
    *,
    model: str,
    width: int,
    height: int,
    steps: int,
    n_samples: int,
    native_payload: dict[str, Any] | None = None,
) -> PricingRule | None:
    """Match a dedicated free package rule (per-model first, then global)."""
    from .key_policy import equivalent_nai_model_ids

    pixels = width * height
    equivalents = equivalent_nai_model_ids(model)
    candidates = [
        rule
        for rule in rules
        if rule.enabled
        and rule.model in {*equivalents, "*"}
        and rule.action in {"generate", "*"}
        and _size_range_matches(rule, pixels=pixels, steps=steps, n_samples=n_samples)
    ]
    candidates.sort(key=lambda rule: _sort_key(rule, model, "generate", equivalents))

    requested_features = extract_pricing_features(native_payload or {}) if native_payload is not None else None
    for rule in candidates:
        if not is_free_package_rule(rule):
            continue
        if pricing_mode(rule.feature_config) != PRICING_MODE_GEMS_FLAT:
            continue
        if not allowed_sizes_match(rule, width, height):
            continue
        if requested_features is not None:
            try:
                require_allowed_features(rule.feature_config, requested_features, base_gems=rule.base_gems)
            except PlatformError:
                continue
        return rule
    return None


def match_pricing_rule(
    rules: list[PricingRule],
    *,
    model: str,
    action: str,
    width: int,
    height: int,
    steps: int,
    n_samples: int,
    native_payload: dict[str, Any] | None = None,
    free_only: bool = False,
) -> PricingRule:
    """Match the best ordinary rule, or raise `PRICE_NOT_CONFIGURED`."""
    from .key_policy import equivalent_nai_model_ids

    pixels = width * height
    equivalents = equivalent_nai_model_ids(model)
    candidates = [
        rule
        for rule in rules
        if rule.enabled
        and rule.model in {*equivalents, "*"}
        and rule.action in {action, "*"}
        and _size_range_matches(rule, pixels=pixels, steps=steps, n_samples=n_samples)
    ]
    candidates.sort(key=lambda rule: _sort_key(rule, model, action, equivalents))

    requested_features = extract_pricing_features(native_payload or {}) if native_payload is not None else None
    rejected_feature_error: PlatformError | None = None
    for rule in candidates:
        if is_system_managed_anlas_rule(rule):
            continue
        config = rule.feature_config
        mode = pricing_mode(config)
        if mode == PRICING_MODE_OFFICIAL_REQUEST_PRICE:
            continue
        if free_only and (mode != PRICING_MODE_GEMS_FLAT or rule.base_gems != 0):
            continue
        if not allowed_sizes_match(rule, width, height):
            continue
        if requested_features is not None:
            try:
                require_allowed_features(
                    config,
                    requested_features,
                    base_gems=1 if mode == PRICING_MODE_ANLAS_FORMULA else rule.base_gems,
                )
            except PlatformError as exc:
                # A protected built-in rule defines the *default* boundary, so its
                # feature rejection means "this rule does not apply" rather than
                # a policy failure.  An administrator-authored rule rejecting a
                # feature is a deliberate policy and must surface.
                if not free_only and not is_built_in_free_policy_rule(rule):
                    rejected_feature_error = rejected_feature_error or exc
                continue
        return rule
    if rejected_feature_error is not None:
        raise rejected_feature_error
    raise PlatformError("PRICE_NOT_CONFIGURED", None, 400)


def free_base_policy_matches(
    rule: PricingRule | None,
    *,
    width: int,
    height: int,
    steps: int,
    n_samples: int,
) -> bool:
    """Match the free boundary (dimensions, steps, samples) without feature gates."""
    if rule is None:
        return False
    if not (is_built_in_free_policy_rule(rule) or is_free_package_rule(rule)) or not rule.enabled:
        return False
    pixels = width * height
    return (
        rule.min_pixels <= pixels <= rule.max_pixels
        and rule.min_steps <= steps <= rule.max_steps
        and rule.min_images <= n_samples <= rule.max_images
        and allowed_sizes_match(rule, width, height)
    )
