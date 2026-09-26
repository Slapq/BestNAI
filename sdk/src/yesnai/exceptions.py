"""Exceptions raised by the YesNAI SDK.

Every error carries the gateway's stable `code` so callers can branch on the
machine-readable reason instead of parsing messages.
"""
from __future__ import annotations

from typing import Any


class YesNAIError(Exception):
    """Base class for all SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.details = details

    def __str__(self) -> str:  # pragma: no cover - trivial
        parts = [self.message]
        if self.code:
            parts.append(f"code={self.code}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return " ".join(parts)


class MissingAPIKeyError(YesNAIError):
    """No gateway credential was supplied."""


class AuthenticationError(YesNAIError):
    """The gateway credential was rejected (HTTP 401)."""


class ForbiddenError(YesNAIError):
    """The credential may not perform the operation (HTTP 403)."""


class InvalidRequestError(YesNAIError):
    """The request was rejected as invalid (HTTP 400/422)."""


class NotFoundError(YesNAIError):
    """The requested resource does not exist (HTTP 404)."""


class ConflictError(YesNAIError):
    """The request conflicts with existing state, e.g. idempotency (HTTP 409)."""


class InsufficientBalanceError(YesNAIError):
    """The upstream key pool cannot fund the request (HTTP 402)."""


class PayloadTooLargeError(YesNAIError):
    """The request body or a decoded image exceeds a limit (HTTP 413)."""


class RateLimitError(YesNAIError):
    """The request was rate limited (HTTP 429)."""


class QueueUnavailableError(YesNAIError):
    """No upstream key became available before the queue deadline (HTTP 503).

    This is the gateway's queue error.  Retrying later is the correct response;
    the request never reached NovelAI.
    """


class ServerError(YesNAIError):
    """The gateway or upstream service failed (HTTP 5xx)."""


class NetworkError(YesNAIError):
    """The gateway could not be reached."""


class StreamError(YesNAIError):
    """A streaming response could not be decoded."""


#: HTTP status -> exception class.
STATUS_EXCEPTIONS: dict[int, type[YesNAIError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    402: InsufficientBalanceError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    413: PayloadTooLargeError,
    422: InvalidRequestError,
    429: RateLimitError,
    500: ServerError,
    502: ServerError,
    503: QueueUnavailableError,
    504: QueueUnavailableError,
}

#: Gateway codes that override the status-based mapping. These are YesNAI's public
#: error codes, so the same SDK works against a YesNAI deployment and against
#: BestNAI, which emits the same vocabulary.
CODE_EXCEPTIONS: dict[str, type[YesNAIError]] = {
    # 400 — request and pricing
    "INVALID_NATIVE_REQUEST": InvalidRequestError,
    "PRICE_NOT_CONFIGURED": InvalidRequestError,
    "PRICING_FEATURE_NOT_ALLOWED": InvalidRequestError,
    "PRICING_FEATURE_REQUIRES_PAID_BASE": InvalidRequestError,
    "INVALID_PRICING_PARAMETER": InvalidRequestError,
    # 401 / 403 — authorization
    "INVALID_API_TOKEN": AuthenticationError,
    "FORBIDDEN": ForbiddenError,
    "MODEL_NOT_ALLOWED": ForbiddenError,
    "TOKEN_FREE_ONLY": ForbiddenError,
    "TOKEN_FREE_TIER_DISABLED": ForbiddenError,
    "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED": ForbiddenError,
    "IMAGE_PACK_DISABLED": ForbiddenError,
    # 402 — balance
    "INSUFFICIENT_GEMS": InsufficientBalanceError,
    "IMAGE_PACK_CREDITS_REQUIRED": InsufficientBalanceError,
    # 404
    "NOT_FOUND": NotFoundError,
    # 409 — idempotency and state
    "IDEMPOTENCY_CONFLICT": ConflictError,
    "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD": ConflictError,
    "DUPLICATE_GENERATION_REQUEST": ConflictError,
    "GENERATION_STATE_CONFLICT": ConflictError,
    "REQUEST_CONFLICT": ConflictError,
    # 413
    "PAYLOAD_TOO_LARGE": PayloadTooLargeError,
    # 429 — rate and quota
    "RATE_LIMITED": RateLimitError,
    "API_TOKEN_DAILY_GEMS_LIMIT_EXCEEDED": RateLimitError,
    "API_TOKEN_TOTAL_GEMS_LIMIT_EXCEEDED": RateLimitError,
    # 503 — the queue and upstream availability
    "UPSTREAM_UNAVAILABLE": QueueUnavailableError,
    "UPSTREAM_TIMEOUT": QueueUnavailableError,
    "NAI5_UNAVAILABLE": QueueUnavailableError,
    # 502 / 500 / 503 — service faults
    "UPSTREAM_INVALID_RESPONSE": ServerError,
    "IMAGE_PROVENANCE_SIGNING_FAILED": ServerError,
    "BILLING_CONFIGURATION_INVALID": ServerError,
    "INTERNAL_ERROR": ServerError,
}


def exception_for(
    status_code: int,
    code: str | None,
    message: str,
    *,
    request_id: str | None = None,
    details: Any = None,
) -> YesNAIError:
    """Build the most specific exception for a gateway error response."""
    cls: type[YesNAIError] | None = None
    if code:
        cls = CODE_EXCEPTIONS.get(code)
    if cls is None:
        cls = STATUS_EXCEPTIONS.get(status_code)
    if cls is None:
        cls = ServerError if status_code >= 500 else YesNAIError
    return cls(
        message,
        status_code=status_code,
        code=code,
        request_id=request_id,
        details=details,
    )
