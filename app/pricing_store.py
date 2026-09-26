"""Pricing rule storage, seeding, and the generation pricing resolution ladder.

The ladder decides *which* rule prices a request:

1. a dedicated free package rule (per-model, then global);
2. otherwise, for NAI5 models, the system-managed Anlas rule is forced;
3. otherwise the configured strategy decides between configured rules and the
   system-managed Anlas fallback.

Free first-image waiving applies only to the system-managed Anlas rule.
"""
from __future__ import annotations

from typing import Any

from .db import Database, dump_json, parse_json_list, parse_json_object, utc_now
from .errors import PlatformError
from .generation import build_pricing_snapshot, extract_job_fields, validate_generation_capabilities
from .key_policy import is_nai5_model
from .pricing import (
    FREE_PACKAGE_BUCKETS,
    PRICING_MODE_ANLAS_FORMULA,
    PRICING_MODE_GEMS_FLAT,
    PRICING_MODE_OFFICIAL_REQUEST_PRICE,
    PRICING_TYPE_ANLAS_FORMULA,
    PRICING_TYPE_FLAT_GEMS,
    PRICING_TYPE_FREE_PACKAGE,
    SYSTEM_ANLAS_BUCKET,
    PricingRule,
    calculate_generation_cost_breakdown,
    free_base_policy_matches,
    free_package_rule_config,
    is_free_package_rule,
    is_system_managed_anlas_rule,
    match_free_package_rule,
    match_pricing_rule,
    pricing_mode,
    rule_from_row,
)
from .site_options import (
    SITE_OPTION_ANLAS_RATIO,
    SITE_OPTION_PRICING_STRATEGY,
    VALID_PRICING_STRATEGIES,
    get_site_options,
)

DEFAULT_FREE_STANDARD_SIZES = ["512×512", "832×1216", "1216×832", "1024×1024"]

_ALLOW_ALL_FEATURES = {
    "allow_i2i": True,
    "allow_inpaint": True,
    "allow_character_reference": True,
    "allow_vibe": True,
    "allow_smea": True,
    "allow_smea_dyn": True,
}

#: Rules that carry a zero Gems flat base may not enable paid features.
PAID_FEATURE_KEYS = (
    "allow_i2i",
    "allow_inpaint",
    "allow_character_reference",
    "allow_vibe",
    "allow_smea",
    "allow_smea_dyn",
)


# ── Reads ─────────────────────────────────────────────────────────────────────


async def load_rules(db: Database) -> list[PricingRule]:
    rows = await db.fetchall("SELECT * FROM pricing_rules ORDER BY priority ASC, id DESC")
    return [
        rule_from_row(row, parse_json_object=parse_json_object, parse_json_list=parse_json_list)
        for row in rows
    ]


async def list_rule_rows(db: Database) -> list[dict[str, Any]]:
    return await db.fetchall("SELECT * FROM pricing_rules ORDER BY priority ASC, id DESC")


async def get_rule_row(db: Database, rule_id: int) -> dict[str, Any] | None:
    return await db.fetchone("SELECT * FROM pricing_rules WHERE id = ?", (rule_id,))


def to_public_rule(row: dict[str, Any]) -> dict[str, Any]:
    rule = rule_from_row(row, parse_json_object=parse_json_object, parse_json_list=parse_json_list)
    public = rule.to_dict()
    public.update(
        {
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "free_package": free_package_rule_config(rule) if is_free_package_rule(rule) else None,
        }
    )
    return public


# ── Seeding ───────────────────────────────────────────────────────────────────


async def seed_built_in_rules(db: Database) -> None:
    """Insert the protected built-in rules once."""
    existing = await db.fetchone(
        "SELECT id FROM pricing_rules WHERE built_in = 1 LIMIT 1"
    )
    if existing is not None:
        return

    now = utc_now()
    anlas_config = {
        "system_managed_anlas": True,
        "pricing_mode": PRICING_MODE_ANLAS_FORMULA,
        "anlas_to_gems_ratio": 1.0,
        **_ALLOW_ALL_FEATURES,
    }
    await db.execute(
        """
        INSERT INTO pricing_rules (
            model, action, size_bucket, min_pixels, max_pixels, min_steps, max_steps,
            min_images, max_images, base_gems, enabled, priority, allowed_sizes,
            feature_config, built_in, created_at, updated_at
        ) VALUES ('*', '*', ?, ?, ?, 1, 50, 1, 8, 0, 1, 10000, '[]', ?, 1, ?, ?)
        """,
        (
            SYSTEM_ANLAS_BUCKET,
            64 * 64,
            3_145_728,
            dump_json(anlas_config),
            now,
            now,
        ),
    )

    free_config = {
        "pricing_mode": PRICING_MODE_GEMS_FLAT,
        "seeded_free_rule": True,
        "is_free_package": True,
        "free_count": 5,
        "free_window_minutes": 60,
        "image_pack_multiplier": 1,
        "allow_i2i": False,
        "allow_inpaint": False,
        "allow_character_reference": False,
        "allow_vibe": False,
        "allow_smea": False,
        "allow_smea_dyn": False,
    }
    await db.execute(
        """
        INSERT INTO pricing_rules (
            model, action, size_bucket, min_pixels, max_pixels, min_steps, max_steps,
            min_images, max_images, base_gems, enabled, priority, allowed_sizes,
            feature_config, built_in, created_at, updated_at
        ) VALUES ('*', 'generate', 'free-standard', ?, ?, 1, 28, 1, 1, 0, 1, 100, ?, ?, 1, ?, ?)
        """,
        (
            512 * 512,
            1216 * 1216,
            dump_json(DEFAULT_FREE_STANDARD_SIZES),
            dump_json(free_config),
            now,
            now,
        ),
    )


# ── Protected rule lookups ────────────────────────────────────────────────────


async def get_built_in_free_policy_rule(db: Database) -> PricingRule | None:
    row = await db.fetchone(
        "SELECT * FROM pricing_rules WHERE built_in = 1 AND size_bucket = 'free-standard' LIMIT 1"
    )
    if row is None:
        return None
    return rule_from_row(row, parse_json_object=parse_json_object, parse_json_list=parse_json_list)


async def get_system_managed_anlas_rule(db: Database) -> PricingRule:
    row = await db.fetchone(
        "SELECT * FROM pricing_rules WHERE size_bucket = ? LIMIT 1",
        (SYSTEM_ANLAS_BUCKET,),
    )
    if row is None:
        raise PlatformError(
            "BILLING_CONFIGURATION_INVALID",
            "The system Anlas fallback rule is missing",
            503,
        )
    return rule_from_row(row, parse_json_object=parse_json_object, parse_json_list=parse_json_list)


async def get_image_pricing_strategy(db: Database) -> tuple[str, float]:
    options = await get_site_options(db)
    strategy = str(options.get(SITE_OPTION_PRICING_STRATEGY) or "configured_rules").strip().lower()
    if strategy not in VALID_PRICING_STRATEGIES:
        raise PlatformError("BILLING_CONFIGURATION_INVALID", None, 503)
    try:
        ratio = float(options.get(SITE_OPTION_ANLAS_RATIO, 1.0))
    except (TypeError, ValueError) as exc:
        raise PlatformError("BILLING_CONFIGURATION_INVALID", None, 503) from exc
    if not (ratio > 0) or ratio != ratio:
        raise PlatformError("BILLING_CONFIGURATION_INVALID", None, 503)
    return strategy, ratio


# ── Resolution ladder ─────────────────────────────────────────────────────────


async def resolve_generation_pricing(
    db: Database,
    native_payload: dict[str, Any],
    *,
    free_base_samples: int | None = None,
) -> tuple[PricingRule, dict[str, Any], dict[str, Any]]:
    """Resolve the pricing rule and breakdown for one native payload.

    Returns `(rule, breakdown, job_fields)`.
    """
    job_fields = extract_job_fields(native_payload)
    model = job_fields["model"]
    action = job_fields["action"]
    width = job_fields["width"]
    height = job_fields["height"]
    steps = job_fields["steps"]
    n_samples = job_fields["n_samples"]

    validate_generation_capabilities(native_payload, model)

    rules = await load_rules(db)
    is_generation_action = action in {"generate", "img2img", "inpaint"}

    rule: PricingRule | None = None
    forced_anlas_ratio: float | None = None

    if is_generation_action:
        rule = match_free_package_rule(
            rules,
            model=model,
            width=width,
            height=height,
            steps=steps,
            n_samples=n_samples,
            native_payload=native_payload,
        )
        if rule is None:
            try:
                rule = match_pricing_rule(
                    rules,
                    model=model,
                    action=action,
                    width=width,
                    height=height,
                    steps=steps,
                    n_samples=n_samples,
                    native_payload=native_payload,
                    free_only=True,
                )
            except PlatformError as exc:
                if exc.code != "PRICE_NOT_CONFIGURED":
                    raise

    if rule is None:
        strategy, ratio = await get_image_pricing_strategy(db)
        if is_generation_action and is_nai5_model(model):
            # NAI5 always routes through the Anlas fallback unless a free
            # package rule already claimed it above.
            rule = await get_system_managed_anlas_rule(db)
            forced_anlas_ratio = ratio
        elif is_generation_action and strategy == "anlas_formula_all":
            rule = await get_system_managed_anlas_rule(db)
            forced_anlas_ratio = ratio
        elif is_generation_action and strategy == "rules_then_anlas":
            try:
                rule = match_pricing_rule(
                    rules,
                    model=model,
                    action=action,
                    width=width,
                    height=height,
                    steps=steps,
                    n_samples=n_samples,
                    native_payload=native_payload,
                )
            except PlatformError as exc:
                if exc.code != "PRICE_NOT_CONFIGURED":
                    raise
                rule = await get_system_managed_anlas_rule(db)
                forced_anlas_ratio = ratio
        else:
            # Non-generation actions require an explicit action-specific rule.
            rule = match_pricing_rule(
                rules,
                model=model,
                action=action,
                width=width,
                height=height,
                steps=steps,
                n_samples=n_samples,
                native_payload=native_payload,
            )

    if free_base_samples is None:
        free_base_samples = 0
        if is_system_managed_anlas_rule(rule):
            free_policy = await get_built_in_free_policy_rule(db)
            if free_base_policy_matches(
                free_policy,
                width=width,
                height=height,
                steps=steps,
                n_samples=n_samples,
            ):
                free_base_samples = 1

    breakdown = calculate_generation_cost_breakdown(
        rule,
        n_samples,
        native_payload,
        model=model,
        width=width,
        height=height,
        steps=steps,
        force_anlas_ratio=forced_anlas_ratio,
        free_base_samples=free_base_samples,
    )
    return rule, breakdown, job_fields


# ── Public quote ──────────────────────────────────────────────────────────────


def public_generation_quote(
    breakdown: dict[str, Any],
    job_fields: dict[str, Any],
) -> dict[str, Any]:
    """The client-facing quote projection."""
    return {
        "model": job_fields.get("model"),
        "action": job_fields.get("action"),
        "width": job_fields.get("width"),
        "height": job_fields.get("height"),
        "steps": job_fields.get("steps"),
        "n_samples": job_fields.get("n_samples"),
        "pricing_mode": breakdown.get("pricing_mode"),
        "is_free_package": breakdown.get("is_free_package", False),
        "total_gems": breakdown.get("total_gems", 0),
        "base_gems": breakdown.get("base_gems", 0),
        "character_reference_gems": breakdown.get("character_reference_gems", 0),
        "vibe_encoding_gems": breakdown.get("vibe_encoding_gems", 0),
        "vibe_reference_gems": breakdown.get("vibe_reference_gems", 0),
        "estimated_anlas": breakdown.get("estimated_anlas"),
        "anlas_to_gems_ratio": breakdown.get("anlas_to_gems_ratio"),
        "anlas_price_multiplier": breakdown.get("anlas_price_multiplier"),
        "per_image_base_gems": breakdown.get("per_image_base_gems", 0),
        "applied_action_strength": breakdown.get("applied_action_strength", 1.0),
        "applied_i2i_multiplier": breakdown.get("applied_i2i_multiplier", 1.0),
        "applied_inpaint_multiplier": breakdown.get("applied_inpaint_multiplier", 1.0),
        "applied_smea_multiplier": breakdown.get("applied_smea_multiplier", 1.0),
        "applied_smea_dyn_multiplier": breakdown.get("applied_smea_dyn_multiplier", 1.0),
        "free_base_applied": breakdown.get("free_base_applied", False),
        "free_base_samples": breakdown.get("free_base_samples", 0),
        "free_base_anlas": breakdown.get("free_base_anlas", 0),
        "free_base_gems": breakdown.get("free_base_gems", 0),
        "features": breakdown.get("features", {}),
        "note": "Anlas values are estimates used for pricing; they are not an official NovelAI charge.",
    }


def public_estimate(quote: dict[str, Any]) -> dict[str, Any]:
    """A narrower projection for unauthenticated estimation."""
    keys = (
        "model",
        "action",
        "width",
        "height",
        "steps",
        "n_samples",
        "pricing_mode",
        "total_gems",
        "base_gems",
        "estimated_anlas",
        "anlas_to_gems_ratio",
        "free_base_applied",
    )
    return {key: quote.get(key) for key in keys}


async def quote_generation(
    db: Database,
    native_payload: dict[str, Any],
) -> dict[str, Any]:
    """Resolve pricing and return the public quote."""
    _rule, breakdown, job_fields = await resolve_generation_pricing(db, native_payload)
    return public_generation_quote(breakdown, job_fields)


# ── Rule administration ───────────────────────────────────────────────────────


def _validate_rule_bounds(payload: dict[str, Any]) -> None:
    if payload["min_pixels"] > payload["max_pixels"]:
        raise PlatformError("INVALID_NATIVE_REQUEST", "min_pixels must not exceed max_pixels", 422)
    if payload["min_steps"] > payload["max_steps"]:
        raise PlatformError("INVALID_NATIVE_REQUEST", "min_steps must not exceed max_steps", 422)
    if payload["min_images"] > payload["max_images"]:
        raise PlatformError("INVALID_NATIVE_REQUEST", "min_images must not exceed max_images", 422)


def _build_feature_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a public pricing_type into the stored feature config."""
    pricing_type = str(payload.get("pricing_type") or PRICING_TYPE_FLAT_GEMS)
    if pricing_type not in {PRICING_TYPE_FREE_PACKAGE, PRICING_TYPE_FLAT_GEMS, PRICING_TYPE_ANLAS_FORMULA}:
        raise PlatformError("INVALID_NATIVE_REQUEST", f"Unknown pricing_type: {pricing_type}", 422)
    if pricing_type == PRICING_TYPE_ANLAS_FORMULA:
        try:
            ratio = float(payload.get("anlas_to_gems_ratio", 1.0))
        except (TypeError, ValueError) as exc:
            raise PlatformError("INVALID_NATIVE_REQUEST", "anlas_to_gems_ratio must be a number", 422) from exc
        if not (ratio > 0):
            raise PlatformError("INVALID_NATIVE_REQUEST", "anlas_to_gems_ratio must be positive", 422)
        return {
            "pricing_mode": PRICING_MODE_ANLAS_FORMULA,
            "anlas_to_gems_ratio": ratio,
            "allow_i2i": True,
            "allow_inpaint": True,
            "allow_character_reference": True,
            "allow_vibe": True,
            "allow_smea": True,
            "allow_smea_dyn": True,
        }

    config: dict[str, Any] = {
        "pricing_mode": PRICING_MODE_GEMS_FLAT,
        "i2i_multiplier": float(payload.get("i2i_multiplier", 1.0)),
        "inpaint_multiplier": float(payload.get("inpaint_multiplier", 1.0)),
        "smea_multiplier": float(payload.get("smea_multiplier", 1.0)),
        "smea_dyn_multiplier": float(payload.get("smea_dyn_multiplier", 1.0)),
        "character_reference_gems": float(payload.get("character_reference_gems", 0.0)),
        "vibe_encoding_gems": float(payload.get("vibe_encoding_gems", 0.0)),
        "vibe_reference_gems": float(payload.get("vibe_reference_gems", 0.0)),
        "vibe_free_references": int(payload.get("vibe_free_references", 4)),
    }
    for key in PAID_FEATURE_KEYS:
        config[key] = bool(payload.get(key, False))

    if pricing_type == PRICING_TYPE_FREE_PACKAGE:
        config["is_free_package"] = True
        config["free_count"] = int(payload.get("free_count", 5))
        config["free_window_minutes"] = int(payload.get("free_window_minutes", 60))
        config["image_pack_multiplier"] = int(payload.get("image_pack_multiplier", 1))

    base_gems = int(payload.get("base_gems", 0))
    if base_gems <= 0 and any(config.get(key) for key in PAID_FEATURE_KEYS):
        enabled = [key for key in PAID_FEATURE_KEYS if config.get(key)]
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            "A zero-Gems flat rule cannot enable paid features: " + ", ".join(enabled),
            422,
        )
    return config


def _rule_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def _int(key: str, default: int) -> int:
        try:
            return int(payload.get(key, default))
        except (TypeError, ValueError) as exc:
            raise PlatformError("INVALID_NATIVE_REQUEST", f"{key} must be an integer", 422) from exc

    model = str(payload.get("model") or "").strip()
    if not model:
        raise PlatformError("INVALID_NATIVE_REQUEST", "model is required", 422)

    sizes = payload.get("allowed_sizes")
    allowed_sizes = [str(item) for item in sizes] if isinstance(sizes, list) else []

    return {
        "model": model,
        "action": str(payload.get("action") or "generate"),
        "size_bucket": str(payload.get("size_bucket") or "default"),
        "min_pixels": _int("min_pixels", 0),
        "max_pixels": _int("max_pixels", 3_145_728),
        "min_steps": _int("min_steps", 1),
        "max_steps": _int("max_steps", 50),
        "min_images": _int("min_images", 1),
        "max_images": _int("max_images", 1),
        "base_gems": _int("base_gems", 0),
        "enabled": bool(payload.get("enabled", True)),
        "priority": _int("priority", 100),
        "allowed_sizes": allowed_sizes,
        "feature_config": _build_feature_config(payload),
    }


async def create_rule(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    values = _rule_payload(payload)
    _validate_rule_bounds(values)
    if values["size_bucket"] in FREE_PACKAGE_BUCKETS or values["size_bucket"] == SYSTEM_ANLAS_BUCKET:
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            f"size_bucket '{values['size_bucket']}' is reserved for built-in rules",
            422,
        )
    now = utc_now()
    rule_id = await db.execute(
        """
        INSERT INTO pricing_rules (
            model, action, size_bucket, min_pixels, max_pixels, min_steps, max_steps,
            min_images, max_images, base_gems, enabled, priority, allowed_sizes,
            feature_config, built_in, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            values["model"],
            values["action"],
            values["size_bucket"],
            values["min_pixels"],
            values["max_pixels"],
            values["min_steps"],
            values["max_steps"],
            values["min_images"],
            values["max_images"],
            values["base_gems"],
            1 if values["enabled"] else 0,
            values["priority"],
            dump_json(values["allowed_sizes"]),
            dump_json(values["feature_config"]),
            now,
            now,
        ),
    )
    row = await get_rule_row(db, rule_id)
    assert row is not None
    return to_public_rule(row)


async def update_rule(db: Database, rule_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    row = await get_rule_row(db, rule_id)
    if row is None:
        raise PlatformError("NOT_FOUND", "Pricing rule not found", 404)
    if row.get("built_in"):
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            "BUILT_IN_PRICING_POLICY_REQUIRES_DEDICATED_ENDPOINT",
            409,
        )
    merged = {**row, **payload}
    values = _rule_payload(merged)
    _validate_rule_bounds(values)
    if values["size_bucket"] in FREE_PACKAGE_BUCKETS or values["size_bucket"] == SYSTEM_ANLAS_BUCKET:
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            f"size_bucket '{values['size_bucket']}' is reserved for built-in rules",
            422,
        )
    await db.execute(
        """
        UPDATE pricing_rules SET
            model = ?, action = ?, size_bucket = ?, min_pixels = ?, max_pixels = ?,
            min_steps = ?, max_steps = ?, min_images = ?, max_images = ?, base_gems = ?,
            enabled = ?, priority = ?, allowed_sizes = ?, feature_config = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            values["model"],
            values["action"],
            values["size_bucket"],
            values["min_pixels"],
            values["max_pixels"],
            values["min_steps"],
            values["max_steps"],
            values["min_images"],
            values["max_images"],
            values["base_gems"],
            1 if values["enabled"] else 0,
            values["priority"],
            dump_json(values["allowed_sizes"]),
            dump_json(values["feature_config"]),
            utc_now(),
            rule_id,
        ),
    )
    updated = await get_rule_row(db, rule_id)
    assert updated is not None
    return to_public_rule(updated)


async def set_rule_enabled(db: Database, rule_id: int, enabled: bool) -> dict[str, Any]:
    row = await get_rule_row(db, rule_id)
    if row is None:
        raise PlatformError("NOT_FOUND", "Pricing rule not found", 404)
    if row.get("built_in"):
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            "BUILT_IN_PRICING_POLICY_REQUIRES_DEDICATED_ENDPOINT",
            409,
        )
    await db.execute(
        "UPDATE pricing_rules SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, utc_now(), rule_id),
    )
    updated = await get_rule_row(db, rule_id)
    assert updated is not None
    return to_public_rule(updated)


async def delete_rule(db: Database, rule_id: int) -> None:
    row = await get_rule_row(db, rule_id)
    if row is None:
        raise PlatformError("NOT_FOUND", "Pricing rule not found", 404)
    if row.get("built_in"):
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            "BUILT_IN_PRICING_POLICY_REQUIRES_DEDICATED_ENDPOINT",
            409,
        )
    await db.execute("DELETE FROM pricing_rules WHERE id = ?", (rule_id,))


async def update_built_in_free_policy(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    row = await db.fetchone(
        "SELECT * FROM pricing_rules WHERE built_in = 1 AND size_bucket = 'free-standard' LIMIT 1"
    )
    if row is None:
        raise PlatformError("BILLING_CONFIGURATION_INVALID", "Built-in free policy is missing", 503)
    config = parse_json_object(row.get("feature_config"))
    if "allowed_sizes" in payload:
        sizes = payload["allowed_sizes"]
        if not isinstance(sizes, list) or not sizes:
            raise PlatformError("INVALID_NATIVE_REQUEST", "allowed_sizes must be a non-empty list", 422)
        await db.execute(
            "UPDATE pricing_rules SET allowed_sizes = ?, updated_at = ? WHERE id = ?",
            (dump_json([str(item) for item in sizes]), utc_now(), row["id"]),
        )
    if "max_steps" in payload:
        config["max_steps"] = int(payload["max_steps"])
    if "max_images" in payload:
        config["max_images"] = int(payload["max_images"])
    if "enabled" in payload:
        await db.execute(
            "UPDATE pricing_rules SET enabled = ?, updated_at = ? WHERE id = ?",
            (1 if payload["enabled"] else 0, utc_now(), row["id"]),
        )
    await db.execute(
        "UPDATE pricing_rules SET feature_config = ?, updated_at = ? WHERE id = ?",
        (dump_json(config), utc_now(), row["id"]),
    )
    updated = await get_rule_row(db, row["id"])
    assert updated is not None
    return to_public_rule(updated)


async def update_anlas_fallback_policy(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    from .site_options import set_site_option

    if "strategy" in payload:
        strategy = str(payload["strategy"]).strip().lower()
        if strategy not in VALID_PRICING_STRATEGIES:
            raise PlatformError(
                "INVALID_NATIVE_REQUEST",
                "strategy must be one of: " + ", ".join(sorted(VALID_PRICING_STRATEGIES)),
                422,
            )
        await set_site_option(db, SITE_OPTION_PRICING_STRATEGY, strategy)
    if "anlas_to_gems_ratio" in payload:
        try:
            ratio = float(payload["anlas_to_gems_ratio"])
        except (TypeError, ValueError) as exc:
            raise PlatformError("INVALID_NATIVE_REQUEST", "anlas_to_gems_ratio must be a number", 422) from exc
        if not (0 < ratio <= 1000):
            raise PlatformError("INVALID_NATIVE_REQUEST", "anlas_to_gems_ratio must be in (0, 1000]", 422)
        await set_site_option(db, SITE_OPTION_ANLAS_RATIO, ratio)
    row = await db.fetchone("SELECT * FROM pricing_rules WHERE size_bucket = ? LIMIT 1", (SYSTEM_ANLAS_BUCKET,))
    if row is None:
        raise PlatformError("BILLING_CONFIGURATION_INVALID", "System Anlas fallback rule is missing", 503)
    return to_public_rule(row)


def snapshot_for_job(
    *,
    job_fields: dict[str, Any],
    breakdown: dict[str, Any],
    rule: PricingRule,
) -> dict[str, Any]:
    return build_pricing_snapshot(
        job_fields=job_fields,
        breakdown=breakdown,
        rule=rule,
    )
