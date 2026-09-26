"""Request normalization, capability validation, and generation job records.

This module owns the boundary between the three input dialects BestNAI accepts
and the single internal *native payload* shape:

* NovelAI-native / low-level (`{"model", "action", "input", "parameters"}`)
* YesNAI high-level convenience params (flat `prompt`/`size`/`steps`/...)
* [OI] Images (`{"model", "prompt", "size": "WxH", "nai": {...}}`)

It also persists generation jobs so the `Idempotency-Key` contract can be
honored across retries and restarts.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .db import Database, dump_json, parse_json_object, utc_now
from .errors import PlatformError
from .key_policy import (
    DEFAULT_IMAGE_DIMENSION_STEP,
    DEFAULT_MAX_IMAGE_DIMENSION,
    DEFAULT_MAX_IMAGE_PIXELS,
    DEFAULT_MIN_IMAGE_DIMENSION,
    PRECISE_REFERENCE_MODELS,
    SMEA_MODELS,
    STRUCTURED_PROMPT_MODELS,
    canonical_model_id,
)

#: Non-canonical feature aliases that must be rejected rather than silently priced.
NONCANONICAL_FEATURE_FIELDS = frozenset({
    "controlnet",
    "vibe_images",
    "vibes",
    "cached_vibe_count",
    "uncached_vibe_count",
})

MAX_IDEMPOTENCY_KEY_LENGTH = 128

#: Marker telling the client layer to use the high-level SDK converter.
REQUEST_FORMAT_KEY = "_yesnovelai_request_format"
REQUEST_FORMAT_HIGH_LEVEL = "bestnai_high_level"

#: Private keys carrying the legacy `-anlas-N` alias budget. They are stripped by
#: `prepare_generation` before a payload is fingerprinted or forwarded upstream.
ALIAS_BUDGET_KEY = "_bestnai_max_anlas"
ALIAS_REQUESTED_MODEL_KEY = "_bestnai_requested_model"
PRIVATE_PAYLOAD_KEYS = frozenset({ALIAS_BUDGET_KEY, ALIAS_REQUESTED_MODEL_KEY})


# ── Payload normalization ─────────────────────────────────────────────────────


def normalize_native_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize any accepted dialect into a native payload.

    Low-level payloads are preserved exactly; only the high-level convenience form
    is rewritten. BestNAI's legacy `-anlas-N` model alias is resolved here so every
    API surface behaves identically and only a canonical model ID is ever sent
    upstream.
    """
    if not isinstance(payload, dict):
        raise PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)

    # Explicit low-level markers: preserve caller fields verbatim.
    if payload.get(REQUEST_FORMAT_KEY) == REQUEST_FORMAT_HIGH_LEVEL:
        normalized = _normalize_high_level(payload)
    elif isinstance(payload.get("parameters"), dict):
        normalized = _normalize_low_level(payload)
    else:
        normalized = _normalize_high_level(payload)
    return _resolve_model_alias(normalized)


def _resolve_model_alias(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip BestNAI's `-anlas-N` / `-points-N` suffix and record the budget."""
    from .model_alias import parse_model_alias

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        return payload
    try:
        parsed = parse_model_alias(model, payload.get("action"), default_max_points=-1)
    except ValueError as exc:
        raise PlatformError("INVALID_NATIVE_REQUEST", str(exc), 400) from exc
    if parsed.requested_model == parsed.base_model:
        return payload
    payload["model"] = parsed.base_model
    payload[ALIAS_BUDGET_KEY] = parsed.max_points
    payload[ALIAS_REQUESTED_MODEL_KEY] = parsed.requested_model
    return payload


def _normalize_low_level(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    data["model"] = canonical_model_id(data.get("model"))
    if data.get("input") is None and data.get("prompt") is not None:
        data["input"] = data["prompt"]
    data.pop("prompt", None)
    data.setdefault("action", "generate")
    parameters = data.get("parameters")
    if not isinstance(parameters, dict):
        raise PlatformError("INVALID_NATIVE_REQUEST", "parameters must be an object", 400)
    data["parameters"] = dict(parameters)
    return data


def _normalize_high_level(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert flat convenience params into a native payload."""
    data = dict(payload)
    model = canonical_model_id(data.get("model"))
    if not model:
        raise PlatformError("INVALID_NATIVE_REQUEST", None, 400)

    size = data.get("size")
    width = data.get("width")
    height = data.get("height")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        width, height = size[0], size[1]
    elif isinstance(size, str) and size:
        parsed = _parse_size_string(size)
        if parsed is not None:
            width, height = parsed

    prompt = data.get("prompt")
    if prompt is None:
        prompt = data.get("input")
    if prompt is None:
        raise PlatformError("INVALID_NATIVE_REQUEST", "prompt is required", 400)

    parameters: dict[str, Any] = {
        "width": int(width or 832),
        "height": int(height or 1216),
        "steps": int(data.get("steps") or 28),
        "n_samples": int(data.get("n_samples") or data.get("n") or 1),
        "sampler": str(data.get("sampler") or "k_euler_ancestral"),
    }

    optional_map = {
        "negative_prompt": "negative_prompt",
        "scale": "scale",
        "seed": "seed",
        "noise_schedule": "noise_schedule",
        "cfg_rescale": "cfg_rescale",
        "image_format": "image_format",
        "sm": "sm",
        "sm_dyn": "sm_dyn",
        "i2i": "i2i",
        "inpaint": "inpaint",
        "image": "image",
        "mask": "mask",
        "character_references": "character_references",
        "director_reference_images": "director_reference_images",
        "reference_image_multiple": "reference_image_multiple",
        "characters": "characters",
        "v4_prompt": "v4_prompt",
        "v4_negative_prompt": "v4_negative_prompt",
    }
    for source, target in optional_map.items():
        if data.get(source) is not None:
            parameters[target] = data[source]

    quality = data.get("quality", data.get("qualityToggle"))
    if quality is not None:
        parameters["qualityToggle"] = bool(quality)

    uc_preset = data.get("uc_preset", data.get("ucPreset"))
    if uc_preset is not None:
        parameters["uc_preset"] = _normalize_uc_preset(uc_preset)

    return {
        "model": model,
        "action": str(data.get("action") or "generate"),
        "input": str(prompt),
        "parameters": parameters,
        REQUEST_FORMAT_KEY: REQUEST_FORMAT_HIGH_LEVEL,
    }


def _parse_size_string(value: str) -> tuple[int, int] | None:
    for separator in ("×", "x", "X", "*"):
        if separator in value:
            left, _, right = value.partition(separator)
            try:
                return int(left.strip()), int(right.strip())
            except ValueError:
                return None
    return None


_UC_PRESET_NAMES = {0: "strong", 1: "light", 2: "furry_focus", 3: "human_focus", 4: "none"}
_UC_PRESET_INTS = {name: number for number, name in _UC_PRESET_NAMES.items()}


def _normalize_uc_preset(value: Any) -> Any:
    if isinstance(value, str):
        return value if value in _UC_PRESET_INTS else "light"
    try:
        return _UC_PRESET_NAMES.get(int(value), "light")
    except (TypeError, ValueError):
        return "light"


def normalize_openai_images_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an [OI] Images request into a native payload.

    The `nai` object carries NovelAI-specific parameters; its fields override the
    [OI] surface fields.
    """
    if not isinstance(payload, dict):
        raise PlatformError("INVALID_NATIVE_REQUEST", "Request body must be a JSON object", 400)

    nai = payload.get("nai")
    nai = dict(nai) if isinstance(nai, dict) else {}

    model = canonical_model_id(nai.get("model") or payload.get("model"))
    if not model:
        raise PlatformError("INVALID_NATIVE_REQUEST", None, 400)

    prompt = nai.get("prompt", payload.get("prompt"))
    if prompt is None:
        raise PlatformError("INVALID_NATIVE_REQUEST", "prompt is required", 400)

    size = payload.get("size", "512x512")
    width, height = (512, 512)
    if isinstance(size, str):
        parsed = _parse_size_string(size)
        if parsed is not None:
            width, height = parsed
    elif isinstance(size, (list, tuple)) and len(size) == 2:
        width, height = int(size[0]), int(size[1])

    n_samples = nai.get("n_samples", payload.get("n", 1))
    try:
        n_samples = int(n_samples)
    except (TypeError, ValueError):
        n_samples = 1

    parameters: dict[str, Any] = {
        "width": int(nai.get("width") or width),
        "height": int(nai.get("height") or height),
        "steps": int(nai.get("steps") or 28),
        "n_samples": n_samples,
        "sampler": str(nai.get("sampler") or "k_euler_ancestral"),
    }
    for source, target in (
        ("negative_prompt", "negative_prompt"),
        ("scale", "scale"),
        ("seed", "seed"),
        ("noise_schedule", "noise_schedule"),
        ("cfg_rescale", "cfg_rescale"),
        ("sm", "sm"),
        ("sm_dyn", "sm_dyn"),
        ("i2i", "i2i"),
        ("inpaint", "inpaint"),
        ("image", "image"),
        ("mask", "mask"),
        ("character_references", "character_references"),
        ("reference_image_multiple", "reference_image_multiple"),
        ("characters", "characters"),
    ):
        if nai.get(source) is not None:
            parameters[target] = nai[source]

    quality = nai.get("qualityToggle", nai.get("quality"))
    if quality is not None:
        parameters["qualityToggle"] = bool(quality)

    uc_preset = nai.get("ucPreset", nai.get("uc_preset"))
    if uc_preset is not None:
        parameters["uc_preset"] = _normalize_uc_preset(uc_preset)

    action = str(nai.get("action") or payload.get("action") or "generate")

    return {
        "model": model,
        "action": action,
        "input": str(prompt),
        "parameters": parameters,
        REQUEST_FORMAT_KEY: REQUEST_FORMAT_HIGH_LEVEL,
    }


# ── Job field extraction and validation ───────────────────────────────────────


def extract_job_fields(native_payload: dict[str, Any]) -> dict[str, Any]:
    """Read and validate the dimension/sample fields used for pricing and routing."""
    parameters = native_payload.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}

    def _positive_int(value: Any, field: str, default: int | None = None) -> int:
        if value is None:
            if default is None:
                raise PlatformError("INVALID_NATIVE_REQUEST", f"{field} is required", 400)
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise PlatformError("INVALID_NATIVE_REQUEST", f"{field} must be an integer", 400) from exc
        if parsed <= 0:
            raise PlatformError("INVALID_NATIVE_REQUEST", f"{field} must be positive", 400)
        return parsed

    model = canonical_model_id(native_payload.get("model"))
    if not model:
        raise PlatformError("INVALID_NATIVE_REQUEST", None, 400)
    action = str(native_payload.get("action") or "generate")
    width = _positive_int(parameters.get("width"), "width")
    height = _positive_int(parameters.get("height"), "height")
    steps = _positive_int(parameters.get("steps"), "steps")
    n_samples = _positive_int(parameters.get("n_samples"), "n_samples", 1)

    if width > DEFAULT_MAX_IMAGE_DIMENSION or height > DEFAULT_MAX_IMAGE_DIMENSION:
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            f"Image dimensions must not exceed {DEFAULT_MAX_IMAGE_DIMENSION} on either side",
            400,
        )
    if width * height > DEFAULT_MAX_IMAGE_PIXELS:
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            f"Image resolution must not exceed {DEFAULT_MAX_IMAGE_PIXELS} pixels",
            400,
        )
    if width < DEFAULT_MIN_IMAGE_DIMENSION or height < DEFAULT_MIN_IMAGE_DIMENSION:
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            f"Image dimensions must be at least {DEFAULT_MIN_IMAGE_DIMENSION}",
            400,
        )
    if width % DEFAULT_IMAGE_DIMENSION_STEP or height % DEFAULT_IMAGE_DIMENSION_STEP:
        raise PlatformError(
            "INVALID_NATIVE_REQUEST",
            f"Image dimensions must be multiples of {DEFAULT_IMAGE_DIMENSION_STEP}",
            400,
        )

    return {
        "model": model,
        "action": action,
        "width": width,
        "height": height,
        "steps": steps,
        "n_samples": n_samples,
    }


def _is_low_level_payload(native_payload: dict[str, Any]) -> bool:
    """Whether this payload is a low-level NovelAI request rather than a high-level one.

    The distinction matters because the two dialects use different field names for
    the same feature: `controlnet` is canonical high-level input, while
    `reference_image_multiple` is canonical low-level input.
    """
    from .client import looks_like_low_level

    return looks_like_low_level(native_payload)


def validate_generation_capabilities(native_payload: dict[str, Any], model: str) -> None:
    """Reject feature/model combinations NovelAI cannot serve."""
    parameters = native_payload.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    canonical = canonical_model_id(model)

    # Non-canonical aliases are only meaningful for low-level payloads, where the
    # canonical field is the upstream REST name.  On the high-level path these
    # same names are the documented SDK fields.
    if _is_low_level_payload(native_payload):
        for field in NONCANONICAL_FEATURE_FIELDS:
            if parameters.get(field) is not None:
                raise PlatformError(
                    "INVALID_NATIVE_REQUEST",
                    f"NONCANONICAL_GENERATION_FEATURE_FIELD:{field}",
                    400,
                )

    has_precise_reference = bool(
        parameters.get("director_reference_images") or parameters.get("character_references")
    )
    if has_precise_reference and canonical not in PRECISE_REFERENCE_MODELS:
        raise PlatformError("INVALID_NATIVE_REQUEST", None, 400)

    if parameters.get("sm") and canonical not in SMEA_MODELS:
        raise PlatformError("INVALID_NATIVE_REQUEST", None, 400)

    characters = parameters.get("characters")
    if isinstance(characters, list) and len(characters) > 1 and canonical not in STRUCTURED_PROMPT_MODELS:
        raise PlatformError("INVALID_NATIVE_REQUEST", None, 400)


# ── Idempotency ───────────────────────────────────────────────────────────────


def normalize_idempotency_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if len(value) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise PlatformError("INVALID_NATIVE_REQUEST", None, 400)
    return value


def request_fingerprint(native_payload: dict[str, Any]) -> str:
    from hashlib import sha256

    canonical = json.dumps(
        native_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


async def find_idempotent_job(
    db: Database,
    *,
    entrypoint: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    return await db.fetchone(
        "SELECT * FROM generation_jobs WHERE entrypoint = ? AND idempotency_key = ? LIMIT 1",
        (entrypoint, idempotency_key),
    )


async def raise_if_duplicate_generation(
    db: Database,
    *,
    entrypoint: str,
    idempotency_key: str | None,
    fingerprint: str | None,
) -> None:
    """Enforce the idempotency contract for one (entrypoint, key) pair."""
    if not idempotency_key:
        return
    existing = await find_idempotent_job(db, entrypoint=entrypoint, idempotency_key=idempotency_key)
    if existing is None:
        return
    if fingerprint and existing.get("request_fingerprint") and existing["request_fingerprint"] != fingerprint:
        raise PlatformError(
            "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
            None,
            409,
            {"job_id": existing.get("job_id")},
        )
    raise PlatformError(
        "DUPLICATE_GENERATION_REQUEST",
        f"DUPLICATE_GENERATION_REQUEST:{existing.get('job_id')}:{existing.get('status')}",
        409,
        {"job_id": existing.get("job_id"), "status": existing.get("status")},
    )


# ── Job records ───────────────────────────────────────────────────────────────


def new_job_id() -> str:
    return uuid.uuid4().hex


def new_request_id() -> str:
    return uuid.uuid4().hex


async def create_job(
    db: Database,
    *,
    job_id: str,
    request_id: str,
    entrypoint: str,
    model: str,
    action: str,
    cost_gems: int,
    estimated_anlas: int,
    pricing_snapshot: dict[str, Any],
    idempotency_key: str | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    try:
        await db.execute(
            """
            INSERT INTO generation_jobs (
                job_id, request_id, entrypoint, idempotency_key, request_fingerprint,
                model, action, status, cost_gems, estimated_anlas,
                pricing_snapshot, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'charged', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                request_id,
                entrypoint,
                idempotency_key,
                fingerprint,
                model,
                action,
                int(cost_gems),
                int(estimated_anlas),
                dump_json(pricing_snapshot),
                now,
                now,
            ),
        )
    except Exception as exc:  # sqlite3.IntegrityError on the idempotency index
        if "UNIQUE" in str(exc).upper():
            await raise_if_duplicate_generation(
                db,
                entrypoint=entrypoint,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
        raise
    row = await db.fetchone("SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,))
    assert row is not None
    return row


async def finalize_job_success(
    db: Database,
    *,
    job_id: str,
    image_count: int,
    duration_ms: int | None = None,
) -> bool:
    """Transition `charged → succeeded`; returns False if already terminal."""
    cursor = await db.execute(
        "UPDATE generation_jobs SET status = 'succeeded', image_count = ?, duration_ms = ?, "
        "updated_at = ? WHERE job_id = ? AND status = 'charged'",
        (int(image_count), duration_ms, utc_now(), job_id),
    )
    row = await db.fetchone("SELECT status FROM generation_jobs WHERE job_id = ?", (job_id,))
    return bool(row and row["status"] == "succeeded" and cursor >= 0)


async def finalize_job_failure(
    db: Database,
    *,
    job_id: str,
    message: str,
    duration_ms: int | None = None,
) -> bool:
    """Transition `charged → failed`; returns False if already terminal."""
    await db.execute(
        "UPDATE generation_jobs SET status = 'failed', error_message = ?, duration_ms = ?, "
        "updated_at = ? WHERE job_id = ? AND status = 'charged'",
        (str(message)[:4000], duration_ms, utc_now(), job_id),
    )
    row = await db.fetchone("SELECT status FROM generation_jobs WHERE job_id = ?", (job_id,))
    return bool(row and row["status"] == "failed")


async def get_job(db: Database, job_id: str) -> dict[str, Any] | None:
    return await db.fetchone("SELECT * FROM generation_jobs WHERE job_id = ?", (job_id,))


def job_summary(row: dict[str, Any]) -> dict[str, Any]:
    """The public projection of a generation job.

    Exactly YesNAI's key set, so clients written against the reference API work
    unchanged. The immutable pricing snapshot is not exposed here; it is available
    to operators through the generation log.
    """
    snapshot = parse_json_object(row.get("pricing_snapshot"))
    request = snapshot.get("request") or {}
    return {
        "job_id": row.get("job_id"),
        "request_id": row.get("request_id"),
        "model": row.get("model"),
        "action": row.get("action"),
        "width": request.get("width"),
        "height": request.get("height"),
        "steps": request.get("steps"),
        "n_samples": request.get("n_samples"),
        "cost_gems": int(row.get("cost_gems") or 0),
        "status": row.get("status"),
    }


def build_pricing_snapshot(
    *,
    job_fields: dict[str, Any],
    breakdown: dict[str, Any],
    rule: Any,
) -> dict[str, Any]:
    """The immutable safe subset of pricing inputs and results."""
    return {
        "version": 1,
        "request": {
            "model": job_fields.get("model"),
            "action": job_fields.get("action"),
            "width": job_fields.get("width"),
            "height": job_fields.get("height"),
            "steps": job_fields.get("steps"),
            "n_samples": job_fields.get("n_samples"),
        },
        "pricing": {
            "pricing_mode": breakdown.get("pricing_mode"),
            "pricing_type": getattr(rule, "pricing_type", None),
            "rule_id": getattr(rule, "id", None),
            "rule_model": getattr(rule, "model", None),
            "rule_action": getattr(rule, "action", None),
            "size_bucket": getattr(rule, "size_bucket", None),
            "total_gems": breakdown.get("total_gems"),
            "base_gems": breakdown.get("base_gems"),
            "estimated_anlas": breakdown.get("estimated_anlas"),
            "anlas_to_gems_ratio": breakdown.get("anlas_to_gems_ratio"),
            "free_base_applied": breakdown.get("free_base_applied", False),
            "is_free_package": breakdown.get("is_free_package", False),
            "character_reference_gems": breakdown.get("character_reference_gems", 0),
            "vibe_encoding_gems": breakdown.get("vibe_encoding_gems", 0),
            "vibe_reference_gems": breakdown.get("vibe_reference_gems", 0),
        },
        "recorded_at": utc_now(),
    }


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)
