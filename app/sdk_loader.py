"""SDK path loader for BestNAI.

The vendored NovelAI SDK is the authoritative source.  Resolution order:

1. ``BESTNAI_SDK_SRC`` (explicit override, highest priority)
2. the repository-vendored ``vendor/novelai-sdk/src``
3. a container path and a couple of sibling-checkout fallbacks

Sibling paths exist only as convenience fallbacks for local development; a
deployment should set ``BESTNAI_SDK_SRC`` explicitly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def sdk_candidates() -> list[Path]:
    env = os.getenv("BESTNAI_SDK_SRC") or os.getenv("NAI_PLATFORM_SDK_SRC")
    repository_root = Path(__file__).resolve().parents[1]
    candidates: list[Path | None] = [
        Path(env) if env else None,
        repository_root / "vendor" / "novelai-sdk" / "src",
        Path("/app/novelai-sdk/src"),
        repository_root.parent / "NAI" / "novelai-sdk" / "src",
    ]
    return [candidate for candidate in candidates if candidate is not None]


def ensure_sdk_on_path() -> Path:
    for candidate in sdk_candidates():
        if candidate.exists():
            resolved = str(candidate.resolve())
            if resolved not in sys.path:
                sys.path.insert(0, resolved)
            return candidate
    tried = "\n".join(f"  {candidate}" for candidate in sdk_candidates())
    raise RuntimeError(
        "novelai-sdk not found. Set BESTNAI_SDK_SRC to its `src` directory.\n"
        f"Tried:\n{tried}"
    )
