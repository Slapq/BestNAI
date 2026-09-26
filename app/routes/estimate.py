"""Estimation and quote surfaces.

Quotes never call upstream and never create a job.  Anlas values are estimates
used for pricing and routing; they are not an official NovelAI charge.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..auth import require_client
from ..errors import PlatformError, openai_error_payload
from ..generation import normalize_native_payload
from ..pricing_store import public_estimate, quote_generation
from .deps import get_db, resolve_upload_refs

router = APIRouter(prefix="/v1", tags=["estimate"])


@router.post("/nai/request-price")
async def ynai_request_price(request: Request, _: str = Depends(require_client)) -> JSONResponse:
    """Local estimate.

    This returns BestNAI's own estimate.  It does not call NovelAI's official
    pricing endpoint, does not generate, and does not charge.
    """
    db = get_db(request)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(
            content=openai_error_payload("INVALID_NATIVE_REQUEST"),
            status_code=400,
        )
    try:
        native_payload = normalize_native_payload(resolve_upload_refs(body))
    except PlatformError as exc:
        return JSONResponse(
            content=openai_error_payload(exc.code, exc.message),
            status_code=exc.status_code,
        )

    try:
        quote = await quote_generation(db, native_payload)
    except PlatformError as exc:
        return JSONResponse(
            content=openai_error_payload(exc.code, exc.message),
            status_code=exc.status_code,
        )

    return JSONResponse(
        content={
            "message": "success",
            "data": {
                **quote,
                "pricing_mode": "bestnai_estimate",
                "estimate_source": "BestNAI",
                "official_price": None,
                "official_price_supported": False,
                "estimated_gems": int(quote.get("total_gems") or 0),
            },
        }
    )


@router.post("/estimate")
async def public_estimate_endpoint(request: Request) -> JSONResponse:
    """Unauthenticated narrow estimate."""
    db = get_db(request)
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(
            content=openai_error_payload("INVALID_NATIVE_REQUEST"),
            status_code=400,
        )
    try:
        native_payload = normalize_native_payload(body)
        quote = await quote_generation(db, native_payload)
    except PlatformError as exc:
        return JSONResponse(
            content=openai_error_payload(exc.code, exc.message),
            status_code=exc.status_code,
        )
    return JSONResponse(content=public_estimate(quote))


@router.get("/pricing/public")
async def public_pricing(request: Request) -> JSONResponse:
    """Unauthenticated pricing overview."""
    from ..key_policy import SUPPORTED_NAI_MODELS
    from ..pricing import (
        PRICING_TYPE_FLAT_GEMS,
        PricingRule,
        free_package_rule_config,
        is_free_package_rule,
        pricing_mode,
    )
    from ..pricing_store import get_built_in_free_policy_rule, load_rules

    db = get_db(request)
    free_rule = await get_built_in_free_policy_rule(db)
    rules = await load_rules(db)

    def describe(rule: PricingRule) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": rule.id,
            "model": rule.model,
            "action": rule.action,
            "size_bucket": rule.size_bucket,
            "priority": rule.priority,
            "enabled": rule.enabled,
            "pricing_type": rule.pricing_type,
            "pricing_mode": pricing_mode(rule.feature_config),
            "base_gems": rule.base_gems,
            "min_pixels": rule.min_pixels,
            "max_pixels": rule.max_pixels,
            "min_steps": rule.min_steps,
            "max_steps": rule.max_steps,
            "min_images": rule.min_images,
            "max_images": rule.max_images,
            "built_in": rule.built_in,
        }
        if is_free_package_rule(rule):
            entry["free_package"] = free_package_rule_config(rule)
        return entry

    return JSONResponse(
        content={
            "models": list(SUPPORTED_NAI_MODELS),
            "free_standard_policy": (
                {
                    "id": free_rule.id,
                    "enabled": free_rule.enabled,
                    "allowed_sizes": free_rule.allowed_sizes,
                    "max_steps": free_rule.max_steps,
                    "max_images": free_rule.max_images,
                    "txt2img_only": True,
                    "advanced_features_allowed": False,
                }
                if free_rule is not None
                else None
            ),
            "rules": [describe(rule) for rule in rules if not rule.built_in],
            "note": (
                "Anlas values are estimates used for platform pricing; "
                "they are not an official NovelAI charge."
            ),
        }
    )
