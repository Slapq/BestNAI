"""Authentication helpers for BestNAI.

Two credential classes exist:

* **client** — may call generation and estimation surfaces.
* **admin** — may additionally manage the key pool, pricing rules, and options.

Credentials are static bearer secrets configured through the environment.  When
no secrets of a class are configured the check passes through, which keeps the
documented local-development mode working; production deployments must configure
secrets and place the service behind a reverse proxy.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Header, Request

from .errors import ErrorCodes, PlatformError


def _get_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise PlatformError(
            code=ErrorCodes.INVALID_API_TOKEN,
            message="Authorization header required",
            status_code=401,
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise PlatformError(
            code=ErrorCodes.INVALID_API_TOKEN,
            message="Authorization header required",
            status_code=401,
        )
    return token


def require_client(
    authorization: Optional[str] = Header(None),
    request: Request = None,
) -> str:
    """Dependency: require a valid client credential."""
    settings = getattr(request.app.state, "settings", None) if request else None
    if settings and not settings.require_auth:
        return "anonymous"
    token = _get_bearer(authorization)
    if settings and settings.client_secrets and token not in settings.client_secrets:
        raise PlatformError(
            code=ErrorCodes.INVALID_API_TOKEN,
            message="Invalid client secret",
            status_code=401,
        )
    return token


def require_admin(
    authorization: Optional[str] = Header(None),
    request: Request = None,
) -> str:
    """Dependency: require a valid admin credential."""
    settings = getattr(request.app.state, "settings", None) if request else None
    token = _get_bearer(authorization)
    if settings and settings.admin_secrets and token not in settings.admin_secrets:
        raise PlatformError(
            code=ErrorCodes.INVALID_API_TOKEN,
            message="Admin access required",
            status_code=403,
        )
    return token
