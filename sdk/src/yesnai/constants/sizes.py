"""Image size constants."""

from __future__ import annotations

from typing import Literal

ImageSizePreset = Literal[
    "portrait",
    "landscape",
    "square",
    "large_portrait",
    "large_landscape",
]
ImageSize = tuple[int, int] | ImageSizePreset

PRESET_MAP: dict[str, tuple[int, int]] = {
    "portrait": (832, 1216),
    "landscape": (1216, 832),
    "square": (1024, 1024),
    "large_portrait": (1024, 1536),
    "large_landscape": (1536, 1024),
}

#: Gateway image bounds.
MIN_DIMENSION = 64
MAX_DIMENSION = 2048
MAX_PIXELS = 3_145_728
DIMENSION_STEP = 64


def resolve_size(size: ImageSize) -> tuple[int, int]:
    """Resolve a preset name or a `(width, height)` pair."""
    if isinstance(size, str):
        try:
            return PRESET_MAP[size]
        except KeyError as exc:
            raise ValueError(
                f"Unknown size preset: {size!r}. Valid presets: {', '.join(PRESET_MAP)}"
            ) from exc
    width, height = size
    return int(width), int(height)


def validate_size(width: int, height: int) -> None:
    """Reject dimensions the gateway will not accept."""
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        raise ValueError(f"Image dimensions must be at least {MIN_DIMENSION}")
    if width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ValueError(f"Image dimensions must not exceed {MAX_DIMENSION}")
    if width * height > MAX_PIXELS:
        raise ValueError(f"Image resolution must not exceed {MAX_PIXELS} pixels")
    if width % DIMENSION_STEP or height % DIMENSION_STEP:
        raise ValueError(f"Image dimensions must be multiples of {DIMENSION_STEP}")
