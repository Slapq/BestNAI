"""Request/response schemas for BestNAI."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    total_keys: int
    busy_keys: int
    idle_keys: int
    disabled_keys: int = 0
    queued_requests: int
    queue_timeout_seconds: float


class KeyStatus(BaseModel):
    key_id: str
    index: int
    masked_key: str
    enabled: bool
    busy: bool
    note: str = ""


class KeyListResponse(BaseModel):
    total_keys: int
    busy_keys: int
    idle_keys: int
    disabled_keys: int
    queued_requests: int
    keys: list[KeyStatus] = Field(default_factory=list)


class KeyUpdateRequest(BaseModel):
    enabled: bool | None = None
    note: str | None = None


class KeyCreateRequest(BaseModel):
    api_key: str
    note: str = ""


class KeyDeleteResponse(BaseModel):
    key: KeyStatus


class KeyUpdateResponse(BaseModel):
    key: KeyStatus


class AnlasBreakdown(BaseModel):
    model: str
    requested_model: str
    resolved_model: str
    size: tuple[int, int] | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    n_samples: int | None = None
    total: int
    base: int
    character_reference: int
    vibe_encoding: int
    vibe_reference: int
    per_image: int
    requested_samples: int
    billable_samples: int
    strength_factor: float
    is_opus: bool
    opus_discount_applied: bool
    allowed: bool
    max_anlas: int
    limit_reason: str | None = None
    notes: list[str] = Field(default_factory=list)
    cached_vibe_count: int = 0
    uncached_vibe_count: int = 0


class EstimateResult(BaseModel):
    requested_model: str
    resolved_model: str
    max_anlas: int
    allowed: bool
    is_opus_assumed: bool
    estimated_anlas: AnlasBreakdown


class GenerateResult(BaseModel):
    requested_model: str
    resolved_model: str
    max_anlas: int
    estimated_anlas: AnlasBreakdown
    image_format: Literal["png", "webp"]
    images: list[str] = Field(default_factory=list)


class UploadResult(BaseModel):
    upload_id: str
    sha256: str
    width: int
    height: int
    size_bytes: int
    mime_type: str
