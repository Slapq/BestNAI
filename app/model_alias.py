"""Model alias parsing for BestNAI."""

from __future__ import annotations

import re
from dataclasses import dataclass


_PATTERN = re.compile(r"^(?P<base>.+?)(?:-(?:points|anlas)-(?P<points>\d+))?$")


@dataclass(slots=True, frozen=True)
class ParsedModelAlias:
    requested_model: str
    base_model: str
    resolved_model: str
    max_points: int


def parse_model_alias(
    model: str | None,
    action: str | None = None,
    default_max_points: int = -1,
) -> ParsedModelAlias:
    if not model:
        raise ValueError("model is required")

    match = _PATTERN.match(model)
    if match is None:
        raise ValueError(f"Invalid model alias: {model}")

    base_model = match.group("base")
    points_raw = match.group("points")
    max_points = int(points_raw) if points_raw is not None else default_max_points
    # Alias can only lower the limit, never raise it above the platform default
    if default_max_points >= 0 and (max_points < 0 or max_points > default_max_points):
        max_points = default_max_points
    resolved_model = base_model

    if action == "infill" and not resolved_model.endswith("-inpainting"):
        resolved_model = f"{resolved_model}-inpainting"

    return ParsedModelAlias(
        requested_model=model,
        base_model=base_model,
        resolved_model=resolved_model,
        max_points=max_points,
    )
