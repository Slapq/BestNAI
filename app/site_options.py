"""Site options: the small set of operator-tunable platform settings.

Options are stored as JSON values keyed by name so new settings can be added
without a schema migration.  `DEFAULT_SITE_OPTIONS` is the authoritative list of
known keys; unknown keys are rejected by the admin API.
"""
from __future__ import annotations

import json
from typing import Any

from .db import Database, dump_json, utc_now

# ── Pricing options ───────────────────────────────────────────────────────────

SITE_OPTION_PRICING_STRATEGY = "ImagePricingStrategy"
SITE_OPTION_ANLAS_RATIO = "ImagePricingAnlasRatio"

#: Valid `ImagePricingStrategy` values.
VALID_PRICING_STRATEGIES = frozenset({
    "configured_rules",
    "anlas_formula_all",
    "rules_then_anlas",
})

# ── Free-tier rate limiting ───────────────────────────────────────────────────

SITE_OPTION_FREE_RATE_LIMIT_ENABLED = "FreeGenerationRateLimitEnabled"
SITE_OPTION_FREE_RATE_LIMIT_COUNT = "FreeGenerationRateLimitCount"
SITE_OPTION_FREE_RATE_LIMIT_MINUTES = "FreeGenerationRateLimitDurationMinutes"

# ── Generation limits ─────────────────────────────────────────────────────────

SITE_OPTION_MAX_STEPS = "GenerationMaxSteps"
SITE_OPTION_MAX_SAMPLES = "GenerationMaxSamples"
SITE_OPTION_MAX_PIXELS = "GenerationMaxPixels"


#: BestNAI defaults to `rules_then_anlas` so the gateway prices every request out
#: of the box: an explicitly configured rule wins, otherwise the built-in Anlas
#: fallback applies.  Operators who want strict rule-only pricing can switch to
#: `configured_rules`, which rejects unpriced requests with PRICE_NOT_CONFIGURED.
DEFAULT_SITE_OPTIONS: dict[str, Any] = {
    SITE_OPTION_PRICING_STRATEGY: "rules_then_anlas",
    SITE_OPTION_ANLAS_RATIO: 1.0,
    SITE_OPTION_FREE_RATE_LIMIT_ENABLED: True,
    SITE_OPTION_FREE_RATE_LIMIT_COUNT: 5,
    SITE_OPTION_FREE_RATE_LIMIT_MINUTES: 60,
    SITE_OPTION_MAX_STEPS: 50,
    SITE_OPTION_MAX_SAMPLES: 8,
    SITE_OPTION_MAX_PIXELS: 3_145_728,
}


async def get_site_options(db: Database) -> dict[str, Any]:
    """Return all site options merged over the defaults."""
    options = dict(DEFAULT_SITE_OPTIONS)
    for row in await db.fetchall("SELECT key, value FROM site_options"):
        try:
            options[row["key"]] = json.loads(row["value"])
        except (TypeError, ValueError):
            options[row["key"]] = row["value"]
    return options


async def get_site_option(db: Database, key: str, default: Any = None) -> Any:
    row = await db.fetchone("SELECT value FROM site_options WHERE key = ?", (key,))
    if row is None:
        return DEFAULT_SITE_OPTIONS.get(key, default)
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


async def set_site_option(db: Database, key: str, value: Any) -> None:
    await db.execute(
        "INSERT INTO site_options (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, dump_json(value), utc_now()),
    )
