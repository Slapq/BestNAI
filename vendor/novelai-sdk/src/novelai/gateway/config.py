"""Configuration helpers for the multi-key gateway."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_api_keys(raw: str | None) -> list[str]:
    if raw is None:
        return []

    keys: list[str] = []
    for chunk in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
        key = chunk.strip()
        if key:
            keys.append(key)
    return keys


@dataclass(slots=True, frozen=True)
class GatewaySettings:
    """Runtime settings for the gateway service."""

    api_keys: list[str]
    queue_timeout_seconds: float = 15.0
    host: str = "0.0.0.0"
    port: int = 8001
    default_is_opus: bool = True

    @classmethod
    def from_env(cls) -> GatewaySettings:
        raw_keys = (
            os.getenv("NAI_GATEWAY_API_KEYS")
            or os.getenv("NOVELAI_API_KEYS")
            or os.getenv("NOVELAI_API_KEY")
        )
        api_keys = _parse_api_keys(raw_keys)
        if not api_keys:
            raise ValueError(
                "No API keys configured. Set NAI_GATEWAY_API_KEYS, "
                "NOVELAI_API_KEYS, or NOVELAI_API_KEY."
            )

        timeout = float(os.getenv("NAI_GATEWAY_QUEUE_TIMEOUT", "15"))
        host = os.getenv("NAI_GATEWAY_HOST", "0.0.0.0")
        port = int(os.getenv("NAI_GATEWAY_PORT", "8001"))

        return cls(
            api_keys=api_keys,
            queue_timeout_seconds=timeout,
            host=host,
            port=port,
            default_is_opus=True,
        )