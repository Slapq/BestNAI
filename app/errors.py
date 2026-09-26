"""Platform error codes, catalog, and response envelopes.

The catalog is **YesNAI's error contract**: the same code strings, HTTP statuses,
and bilingual messages, so a client written against YesNAI works unchanged against
BestNAI.

BestNAI is a gateway without user accounts, Gems wallets, or image packs, so it
emits only the subset of the catalog it can actually produce. The remaining codes
are defined here because clients must be able to interpret them when the same SDK
is pointed at a full YesNAI deployment.

Four envelope shapes exist, one per API dialect:

* `platform`  — BestNAI administration: `{"error": {code, message, ...}}`
* `openai`    — [OI]-compatible `/v1/*`: `{"error": {message, type, code}}`
* `native`    — NovelAI-native `/native/*`: `{"message", "code", "request_id"}`
* `launcher`  — Aaalice Launcher `/ai/*`, `/user/*`: `{"message", "code"}`
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PublicError:
    """A stable public error: HTTP status, machine code, and bilingual message."""

    status_code: int
    code: str
    message_en: str
    message_zh: str

    def message(self, language: str = "en") -> str:
        return self.message_zh if str(language).startswith("zh") else self.message_en


#: YesNAI's public error catalog, reproduced exactly.
ERROR_CATALOG: dict[str, PublicError] = {
    "INVALID_API_TOKEN": PublicError(
        401, "INVALID_API_TOKEN",
        "The API Token is invalid or disabled.", "API Token 无效或已禁用。",
    ),
    "INSUFFICIENT_GEMS": PublicError(
        402, "INSUFFICIENT_GEMS", "The Gems balance is insufficient.", "Gems 余额不足。",
    ),
    "MODEL_NOT_ALLOWED": PublicError(
        403, "MODEL_NOT_ALLOWED",
        "This API Token is not allowed to use the requested model.",
        "当前 API Token 无权使用所请求的模型。",
    ),
    "TOKEN_FREE_ONLY": PublicError(
        403, "TOKEN_FREE_ONLY",
        "This API Token only allows requests matched by free rules.",
        "此 API Token 仅允许命中免费规则的请求。",
    ),
    "TOKEN_FREE_TIER_DISABLED": PublicError(
        403, "TOKEN_FREE_TIER_DISABLED",
        "This API Token does not allow free-rule requests.",
        "此 API Token 已禁止免费规则请求。",
    ),
    "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED": PublicError(
        403, "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED",
        "This request exceeds the API Token per-request Gems limit.",
        "本次请求费用超过 API Token 的单次 Gems 上限。",
    ),
    "API_TOKEN_DAILY_GEMS_LIMIT_EXCEEDED": PublicError(
        429, "API_TOKEN_DAILY_GEMS_LIMIT_EXCEEDED",
        "This API Token has reached its daily Gems limit.",
        "此 API Token 今日 Gems 额度已用完。",
    ),
    "API_TOKEN_TOTAL_GEMS_LIMIT_EXCEEDED": PublicError(
        429, "API_TOKEN_TOTAL_GEMS_LIMIT_EXCEEDED",
        "This API Token has reached its total Gems limit.",
        "此 API Token 的总 Gems 额度已用完。",
    ),
    "IMAGE_PACK_CREDITS_REQUIRED": PublicError(
        402, "IMAGE_PACK_CREDITS_REQUIRED",
        "The free allowance is exhausted. Purchase image-pack credits before retrying.",
        "免费额度已用完，请先购买图片包额度后重试。",
    ),
    "IMAGE_PACK_DISABLED": PublicError(
        403, "IMAGE_PACK_DISABLED",
        "Image-pack credits are currently disabled.", "图片包额度功能当前未启用。",
    ),
    "PRICE_NOT_CONFIGURED": PublicError(
        400, "PRICE_NOT_CONFIGURED",
        "Pricing is not configured for this request.", "该请求暂未配置价格。",
    ),
    "BILLING_CONFIGURATION_INVALID": PublicError(
        503, "BILLING_CONFIGURATION_INVALID",
        "The billing service is misconfigured. Please retry later.",
        "计费服务配置异常，请稍后重试。",
    ),
    "NAI5_UNAVAILABLE": PublicError(
        503, "NAI5_UNAVAILABLE",
        "NAI5 is temporarily unavailable. Please retry later.", "NAI5 暂时不可用，请稍后重试。",
    ),
    "UPSTREAM_TIMEOUT": PublicError(
        503, "UPSTREAM_TIMEOUT",
        "The upstream image service timed out. Please retry later.",
        "上游图像服务请求超时，请稍后重试。",
    ),
    "UPSTREAM_UNAVAILABLE": PublicError(
        503, "UPSTREAM_UNAVAILABLE",
        "The upstream image service is temporarily unavailable. Please retry later.",
        "上游图像服务暂时不可用，请稍后重试。",
    ),
    "UPSTREAM_INVALID_RESPONSE": PublicError(
        502, "UPSTREAM_INVALID_RESPONSE",
        "The upstream image service returned an invalid response.",
        "上游图像服务返回了无效响应。",
    ),
    "IMAGE_PROVENANCE_SIGNING_FAILED": PublicError(
        502, "IMAGE_PROVENANCE_SIGNING_FAILED",
        "The generated image could not be finalized. Please retry later.",
        "生成图片无法完成签名，请稍后重试。",
    ),
    "IDEMPOTENCY_CONFLICT": PublicError(
        409, "IDEMPOTENCY_CONFLICT",
        "This idempotency key was already used for another generation request.",
        "该幂等键已用于另一个生成请求。",
    ),
    "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD": PublicError(
        409, "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
        "This idempotency key was already used with different parameters.",
        "该幂等键已被不同参数使用。",
    ),
    "DUPLICATE_GENERATION_REQUEST": PublicError(
        409, "DUPLICATE_GENERATION_REQUEST",
        "This generation request was already processed and will not be charged again.",
        "该生成请求已处理，不会再次扣费。",
    ),
    "GENERATION_STATE_CONFLICT": PublicError(
        409, "GENERATION_STATE_CONFLICT",
        "The generation job state has changed. Do not submit it again.",
        "生成任务状态已变化，请勿重复提交。",
    ),
    "PAYLOAD_TOO_LARGE": PublicError(
        413, "PAYLOAD_TOO_LARGE", "The request body is too large.", "请求内容过大。",
    ),
    "RATE_LIMITED": PublicError(
        429, "RATE_LIMITED", "Too many requests. Please retry later.", "请求过于频繁，请稍后重试。",
    ),
    "PRICING_FEATURE_NOT_ALLOWED": PublicError(
        400, "PRICING_FEATURE_NOT_ALLOWED",
        "The matched pricing rule does not allow one of the requested features.",
        "命中的定价规则不允许请求中的某项功能。",
    ),
    "PRICING_FEATURE_REQUIRES_PAID_BASE": PublicError(
        400, "PRICING_FEATURE_REQUIRES_PAID_BASE",
        "This feature requires a paid pricing rule.", "此功能需要使用付费定价规则。",
    ),
    "INVALID_PRICING_PARAMETER": PublicError(
        400, "INVALID_PRICING_PARAMETER",
        "One of the pricing parameters is invalid.", "某个定价参数无效。",
    ),
    "INVALID_NATIVE_REQUEST": PublicError(
        400, "INVALID_NATIVE_REQUEST", "The request parameters are invalid.", "请求参数错误。",
    ),
    "FORBIDDEN": PublicError(
        403, "FORBIDDEN", "This operation is not permitted.", "当前操作没有权限。",
    ),
    "NOT_FOUND": PublicError(
        404, "NOT_FOUND", "The requested resource was not found.", "未找到请求的资源。",
    ),
    "REQUEST_CONFLICT": PublicError(
        409, "REQUEST_CONFLICT",
        "The request conflicts with the current resource state.", "请求与资源当前状态冲突。",
    ),
    "INTERNAL_ERROR": PublicError(
        500, "INTERNAL_ERROR",
        "The server encountered an internal error.", "服务器内部错误。",
    ),
}

#: Fallback code when only an HTTP status is known. Mirrors YesNAI exactly.
GENERIC_CODES_BY_STATUS: dict[int, str] = {
    400: "INVALID_NATIVE_REQUEST",
    401: "INVALID_API_TOKEN",
    402: "INSUFFICIENT_GEMS",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "REQUEST_CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
    502: "UPSTREAM_INVALID_RESPONSE",
    503: "UPSTREAM_UNAVAILABLE",
}

#: Internal detail strings that map onto a catalog code.
DETAIL_ALIASES: dict[str, str] = {
    "API token is disabled or missing": "INVALID_API_TOKEN",
    "API token user is disabled or missing": "INVALID_API_TOKEN",
    "Valid API token required": "INVALID_API_TOKEN",
    "Model is not allowed for this API token": "MODEL_NOT_ALLOWED",
    "Insufficient Gems balance": "INSUFFICIENT_GEMS",
    "API_TOKEN_DAILY_LIMIT_EXCEEDED": "API_TOKEN_DAILY_GEMS_LIMIT_EXCEEDED",
    "API_TOKEN_TOTAL_LIMIT_EXCEEDED": "API_TOKEN_TOTAL_GEMS_LIMIT_EXCEEDED",
    "NAI_KEY_INVALID": "UPSTREAM_UNAVAILABLE",
    "NAI_INSUFFICIENT_CREDITS_OR_SUBSCRIPTION": "UPSTREAM_UNAVAILABLE",
    "GENERATION_JOB_NOT_CHARGED": "GENERATION_STATE_CONFLICT",
    "NAI5_ANLAS_REPRICE_NOT_APPLICABLE": "GENERATION_STATE_CONFLICT",
}

#: Parameterised codes (`CODE:suffix`) keep their suffix when rendered.
STABLE_CODE_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]+)(?::|$)")

#: Upstream failures that always mean "the pool could not serve this request".
UPSTREAM_FAILURE_STATUSES = frozenset({401, 402, 429, 503, 504})

#: Upstream error code -> public code.
UPSTREAM_CODE_MAP: dict[str, str] = {
    # A rejected credential is a server-side problem: the caller cannot fix it.
    "NAI_KEY_INVALID": "UPSTREAM_UNAVAILABLE",
    # A key without Anlas is a pool balance problem, and 402 says so precisely.
    "NAI_INSUFFICIENT_CREDITS_OR_SUBSCRIPTION": "INSUFFICIENT_GEMS",
}


class ErrorCodes:
    """Named access to the codes BestNAI emits, for internal call sites."""

    INVALID_API_TOKEN = "INVALID_API_TOKEN"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    INSUFFICIENT_GEMS = "INSUFFICIENT_GEMS"
    TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED = "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED"
    PRICE_NOT_CONFIGURED = "PRICE_NOT_CONFIGURED"
    PRICING_FEATURE_NOT_ALLOWED = "PRICING_FEATURE_NOT_ALLOWED"
    PRICING_FEATURE_REQUIRES_PAID_BASE = "PRICING_FEATURE_REQUIRES_PAID_BASE"
    INVALID_PRICING_PARAMETER = "INVALID_PRICING_PARAMETER"
    INVALID_NATIVE_REQUEST = "INVALID_NATIVE_REQUEST"
    BILLING_CONFIGURATION_INVALID = "BILLING_CONFIGURATION_INVALID"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    REQUEST_CONFLICT = "REQUEST_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD = "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD"
    DUPLICATE_GENERATION_REQUEST = "DUPLICATE_GENERATION_REQUEST"
    GENERATION_STATE_CONFLICT = "GENERATION_STATE_CONFLICT"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    UPSTREAM_INVALID_RESPONSE = "UPSTREAM_INVALID_RESPONSE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PlatformError(Exception):
    """A public, catalog-backed platform error."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = ERROR_CATALOG.get(code)
        self.code = code
        self.status_code = (
            status_code if status_code is not None else (entry.status_code if entry else 500)
        )
        if message is not None:
            self.message = message
        elif entry is not None:
            self.message = entry.message_en
        else:
            self.message = code
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)

    def localized_message(self, language: str = "en") -> str:
        entry = ERROR_CATALOG.get(self.code)
        if entry is not None and self.message == entry.message_en:
            return entry.message(language)
        return self.message


# ── Code resolution ───────────────────────────────────────────────────────────


def error_code_from_detail(detail: Any) -> str:
    """Extract a catalog code from a detail string or dict."""
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str) and code.strip():
            return DETAIL_ALIASES.get(code.strip(), code.strip())
        message = detail.get("message")
        if isinstance(message, str):
            return error_code_from_detail(message)
        return ""
    if not isinstance(detail, str):
        return ""
    normalized = detail.strip()
    alias = DETAIL_ALIASES.get(normalized)
    if alias:
        return alias
    match = STABLE_CODE_PATTERN.match(normalized)
    if not match:
        return ""
    code = match.group(1)
    return DETAIL_ALIASES.get(code, code)


def error_code_with_context(detail: Any, fallback_code: str) -> str:
    """Preserve a `CODE:suffix` parameterised detail when present."""
    if isinstance(detail, str):
        normalized = detail.strip()
        match = STABLE_CODE_PATTERN.match(normalized)
        if match and normalized.startswith(f"{match.group(1)}:"):
            return normalized
    return fallback_code


def message_from_detail(detail: Any) -> str:
    if isinstance(detail, dict):
        message = detail.get("message")
        return message.strip() if isinstance(message, str) else ""
    return detail.strip() if isinstance(detail, str) else ""


def resolve_public_error(
    detail: Any,
    status_code: int,
    *,
    language: str = "en",
    generic_unknown: bool = True,
) -> tuple[int, str, str]:
    """Resolve `(status, code, message)` from a detail value and status.

    A catalogued code wins and overrides the incoming status. An unknown but
    stable-looking code is passed through when `generic_unknown` is false.
    """
    code = error_code_from_detail(detail)
    catalogued = ERROR_CATALOG.get(code)
    if catalogued is not None:
        return catalogued.status_code, catalogued.code, catalogued.message(language)

    generic_code = GENERIC_CODES_BY_STATUS.get(status_code, "INTERNAL_ERROR")
    generic = ERROR_CATALOG[generic_code]
    if generic_unknown:
        return generic.status_code, generic.code, generic.message(language)
    if code:
        return generic.status_code, code, generic.message(language)

    message = message_from_detail(detail)
    return status_code, generic.code, message or generic.message(language)


def status_for_code(code: str, fallback: int = 500) -> int:
    entry = ERROR_CATALOG.get(code)
    return entry.status_code if entry is not None else fallback


def message_for_code(code: str, language: str = "en") -> str:
    entry = ERROR_CATALOG.get(code)
    return entry.message(language) if entry is not None else code


def resolve_upstream_public_error(
    upstream_code: str | None,
    upstream_status: int | None,
    *,
    language: str = "en",
) -> tuple[int, str, str]:
    """Map an upstream failure onto `(status, code, message)`."""
    normalized = str(upstream_code or "").strip()
    mapped = UPSTREAM_CODE_MAP.get(normalized)
    if mapped is not None:
        return status_for_code(mapped), mapped, message_for_code(mapped, language)
    if upstream_status in UPSTREAM_FAILURE_STATUSES:
        code = "UPSTREAM_UNAVAILABLE"
        return status_for_code(code), code, message_for_code(code, language)
    code = "UPSTREAM_INVALID_RESPONSE"
    return status_for_code(code), code, message_for_code(code, language)


# ── Envelopes ─────────────────────────────────────────────────────────────────


def platform_error_payload(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """BestNAI administration envelope."""
    return {
        "error": {
            "code": code,
            "message": message,
            "status_code": status_code,
            "details": details or {},
        }
    }


def openai_error_payload(
    code: str,
    message: str | None = None,
    *,
    language: str = "en",
    err_type: str | None = None,
) -> dict[str, Any]:
    """[OI]-compatible envelope. Language is English, matching YesNAI."""
    status = status_for_code(code)
    resolved_type = err_type or ("server_error" if status >= 500 else "invalid_request_error")
    return {
        "error": {
            "message": message if message is not None else message_for_code(code, "en"),
            "type": resolved_type,
            "code": code,
        }
    }


def native_error_payload(
    code: str,
    message: str | None = None,
    *,
    request_id: str | None = None,
    language: str = "zh",
) -> dict[str, Any]:
    """NovelAI-native envelope."""
    payload: dict[str, Any] = {
        "message": message if message is not None else message_for_code(code, language),
        "code": code,
    }
    if request_id:
        payload["request_id"] = request_id
    return payload


def launcher_error_payload(
    code: str,
    message: str | None = None,
    *,
    language: str = "zh",
) -> dict[str, Any]:
    """Aaalice Launcher envelope."""
    return {
        "message": message if message is not None else message_for_code(code, language),
        "code": code,
    }


#: Historical alias kept for callers that imported the old name.
def error_payload(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return platform_error_payload(code, message, status_code, details)


__all__ = [
    "DETAIL_ALIASES",
    "ERROR_CATALOG",
    "ErrorCodes",
    "GENERIC_CODES_BY_STATUS",
    "PlatformError",
    "PublicError",
    "STABLE_CODE_PATTERN",
    "UPSTREAM_CODE_MAP",
    "UPSTREAM_FAILURE_STATUSES",
    "error_code_from_detail",
    "error_code_with_context",
    "error_payload",
    "launcher_error_payload",
    "message_for_code",
    "message_from_detail",
    "native_error_payload",
    "openai_error_payload",
    "platform_error_payload",
    "resolve_public_error",
    "resolve_upstream_public_error",
    "status_for_code",
]
