"""Configuration models for BestNAI."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def parse_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [
        item.strip()
        for item in raw.replace("\r", "\n").replace(",", "\n").split("\n")
        if item.strip()
    ]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def default_database_path() -> Path:
    override = os.getenv("BESTNAI_DB_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data" / "bestnai.db"


@dataclass(slots=True)
class Settings:
    api_keys: list[str] = field(default_factory=list)
    client_secrets: list[str] = field(default_factory=list)
    admin_secrets: list[str] = field(default_factory=list)
    cors_origins: list[str] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 8010
    queue_timeout_seconds: float = 30.0
    default_max_anlas: int = -1
    default_is_opus: bool = True
    require_auth: bool = True
    max_request_bytes: int = 256 * 1024 * 1024
    max_upload_bytes: int = 20 * 1024 * 1024
    max_image_store_entries: int = 500
    # ── Dynamic key pool ─────────────────────────────────────────────────────
    database_path: Path = field(default_factory=default_database_path)
    dynamic_pool_enabled: bool = True
    key_sync_interval_seconds: float = 30.0
    account_check_timeout_seconds: float = 30.0
    # ── Upstream endpoints ───────────────────────────────────────────────────
    novelai_image_base: str = "https://image.novelai.net"
    novelai_api_base: str = "https://api.novelai.net"
    # ── Presentation ─────────────────────────────────────────────────────────
    default_language: str = "zh"
    # ── Free-tier rate limiting ──────────────────────────────────────────────
    free_rate_limit_enabled: bool = True
    free_rate_limit_count: int = 5
    free_rate_limit_minutes: int = 60
    # ── Provenance ───────────────────────────────────────────────────────────
    provenance_signing_enabled: bool = False
    provenance_signing_secret: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        api_keys = parse_list(
            os.getenv("BESTNAI_API_KEYS")
            or os.getenv("NAI_PLATFORM_API_KEYS")
            or os.getenv("NAI_GATEWAY_API_KEYS")
            or os.getenv("NOVELAI_API_KEYS")
            or os.getenv("NOVELAI_API_KEY")
        )
        client_secrets = parse_list(os.getenv("BESTNAI_CLIENT_SECRETS") or os.getenv("VALID_SECRETS"))
        admin_secrets = parse_list(os.getenv("BESTNAI_ADMIN_SECRETS") or os.getenv("ADMIN_SECRETS"))
        cors_origins = parse_list(os.getenv("BESTNAI_CORS_ORIGINS") or os.getenv("CORS_ORIGINS"))

        dynamic_pool_enabled = _env_bool("BESTNAI_DYNAMIC_POOL", True)
        if not api_keys and not dynamic_pool_enabled:
            raise ValueError(
                "No NovelAI API keys configured for BestNAI. "
                "Set BESTNAI_API_KEYS or enable BESTNAI_DYNAMIC_POOL and add keys via the admin API."
            )

        return cls(
            api_keys=api_keys,
            client_secrets=client_secrets,
            admin_secrets=admin_secrets,
            cors_origins=cors_origins,
            host=os.getenv("BESTNAI_HOST", "127.0.0.1"),
            port=int(os.getenv("BESTNAI_PORT", "8010")),
            queue_timeout_seconds=float(os.getenv("BESTNAI_QUEUE_TIMEOUT", "30")),
            default_max_anlas=int(os.getenv("BESTNAI_DEFAULT_MAX_ANLAS", "-1")),
            default_is_opus=_env_bool("BESTNAI_DEFAULT_IS_OPUS", True),
            require_auth=_env_bool("BESTNAI_REQUIRE_AUTH", True),
            max_request_bytes=int(os.getenv("BESTNAI_MAX_REQUEST_BYTES", str(256 * 1024 * 1024))),
            max_upload_bytes=int(os.getenv("BESTNAI_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))),
            max_image_store_entries=int(os.getenv("BESTNAI_MAX_IMAGE_STORE_ENTRIES", "500")),
            database_path=default_database_path(),
            dynamic_pool_enabled=dynamic_pool_enabled,
            key_sync_interval_seconds=float(os.getenv("BESTNAI_KEY_SYNC_INTERVAL_SECONDS", "30")),
            account_check_timeout_seconds=float(os.getenv("BESTNAI_ACCOUNT_CHECK_TIMEOUT", "30")),
            novelai_image_base=os.getenv("BESTNAI_NOVELAI_IMAGE_BASE", "https://image.novelai.net"),
            novelai_api_base=os.getenv("BESTNAI_NOVELAI_API_BASE", "https://api.novelai.net"),
            default_language=os.getenv("BESTNAI_LANGUAGE", "zh"),
            free_rate_limit_enabled=_env_bool("BESTNAI_FREE_RATE_LIMIT_ENABLED", True),
            free_rate_limit_count=int(os.getenv("BESTNAI_FREE_RATE_LIMIT_COUNT", "5")),
            free_rate_limit_minutes=int(os.getenv("BESTNAI_FREE_RATE_LIMIT_MINUTES", "60")),
            provenance_signing_enabled=_env_bool("BESTNAI_PROVENANCE_SIGNING", False),
            provenance_signing_secret=os.getenv("BESTNAI_PROVENANCE_SECRET", ""),
        )
