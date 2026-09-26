"""Verify BestNAI's public contract matches YesNAI's.

These checks are about *compatibility*, not behaviour: error code strings, the
generic status fallback table, envelope shapes, ZIP entry naming, job summary
keys, and subscription payload keys. They are written against the reference
contract so a client built for YesNAI works against BestNAI unchanged.
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile

os.environ["BESTNAI_DYNAMIC_POOL"] = "true"
os.environ["BESTNAI_REQUIRE_AUTH"] = "false"
os.environ["BESTNAI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "align.db")
os.environ["BESTNAI_ADMIN_SECRETS"] = "admin-secret"
os.environ["BESTNAI_CLIENT_SECRETS"] = "client-secret"
os.environ["BESTNAI_API_KEYS"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.errors import (  # noqa: E402
    ERROR_CATALOG,
    GENERIC_CODES_BY_STATUS,
    resolve_public_error,
    resolve_upstream_public_error,
)
from app.main import app  # noqa: E402

ADMIN = {"Authorization": "Bearer admin-secret"}
CLIENT = {"Authorization": "Bearer client-secret"}

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"  <- {detail}"))
    if not ok:
        failures.append(label)


# ── YesNAI's error catalog, reproduced from the reference contract ────────────
YESNAI_CATALOG: dict[str, int] = {
    "INVALID_API_TOKEN": 401,
    "INSUFFICIENT_GEMS": 402,
    "MODEL_NOT_ALLOWED": 403,
    "TOKEN_FREE_ONLY": 403,
    "TOKEN_FREE_TIER_DISABLED": 403,
    "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED": 403,
    "API_TOKEN_DAILY_GEMS_LIMIT_EXCEEDED": 429,
    "API_TOKEN_TOTAL_GEMS_LIMIT_EXCEEDED": 429,
    "IMAGE_PACK_CREDITS_REQUIRED": 402,
    "IMAGE_PACK_DISABLED": 403,
    "PRICE_NOT_CONFIGURED": 400,
    "BILLING_CONFIGURATION_INVALID": 503,
    "NAI5_UNAVAILABLE": 503,
    "UPSTREAM_TIMEOUT": 503,
    "UPSTREAM_UNAVAILABLE": 503,
    "UPSTREAM_INVALID_RESPONSE": 502,
    "IMAGE_PROVENANCE_SIGNING_FAILED": 502,
    "IDEMPOTENCY_CONFLICT": 409,
    "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD": 409,
    "DUPLICATE_GENERATION_REQUEST": 409,
    "GENERATION_STATE_CONFLICT": 409,
    "PAYLOAD_TOO_LARGE": 413,
    "RATE_LIMITED": 429,
    "PRICING_FEATURE_NOT_ALLOWED": 400,
    "PRICING_FEATURE_REQUIRES_PAID_BASE": 400,
    "INVALID_PRICING_PARAMETER": 400,
    "INVALID_NATIVE_REQUEST": 400,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "REQUEST_CONFLICT": 409,
    "INTERNAL_ERROR": 500,
}

YESNAI_GENERIC: dict[int, str] = {
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

YESNAI_JOB_KEYS = {
    "job_id", "request_id", "model", "action",
    "width", "height", "steps", "n_samples", "cost_gems", "status",
}

YESNAI_PERKS_KEYS = {
    "maxPriorityActions", "startPriority", "moduleTrainingSteps",
    "unlimitedMaxPriority", "voiceGeneration", "imageGeneration",
    "unlimitedImageGeneration", "unlimitedImageGenerationLimits", "contextTokens",
}


def test_catalog() -> None:
    missing = {code: status for code, status in YESNAI_CATALOG.items() if code not in ERROR_CATALOG}
    check("every YesNAI error code is defined", not missing, missing)

    wrong = {
        code: (ERROR_CATALOG[code].status_code, expected)
        for code, expected in YESNAI_CATALOG.items()
        if code in ERROR_CATALOG and ERROR_CATALOG[code].status_code != expected
    }
    check("every error code has YesNAI's HTTP status", not wrong, wrong)

    check("generic status fallback matches YesNAI",
          GENERIC_CODES_BY_STATUS == YESNAI_GENERIC,
          {k: (GENERIC_CODES_BY_STATUS.get(k), v) for k, v in YESNAI_GENERIC.items()
           if GENERIC_CODES_BY_STATUS.get(k) != v})

    # A catalogued code overrides the incoming status.
    status, code, _ = resolve_public_error("TOKEN_FREE_ONLY", 400, generic_unknown=False)
    check("catalogued code overrides the incoming status",
          (status, code) == (403, "TOKEN_FREE_ONLY"), (status, code))

    # A non-code human string keeps its message and takes the generic code.
    status, code, message = resolve_public_error("something went wrong", 429, generic_unknown=False)
    check("a human-readable detail keeps its message",
          (status, code, message) == (429, "RATE_LIMITED", "something went wrong"),
          (status, code, message))

    # YesNAI's internal detail aliases resolve to catalog codes.
    for alias, expected in (
        ("Valid API token required", "INVALID_API_TOKEN"),
        ("Insufficient Gems balance", "INSUFFICIENT_GEMS"),
        ("Model is not allowed for this API token", "MODEL_NOT_ALLOWED"),
        ("NAI_KEY_INVALID", "UPSTREAM_UNAVAILABLE"),
    ):
        _, code, _ = resolve_public_error(alias, 400, generic_unknown=False)
        check(f"detail alias {alias!r} -> {expected}", code == expected, code)

    # Upstream mapping
    for upstream, expected in (
        ("NAI_KEY_INVALID", "UPSTREAM_UNAVAILABLE"),
        ("NAI_INSUFFICIENT_CREDITS_OR_SUBSCRIPTION", "INSUFFICIENT_GEMS"),
    ):
        _, code, _ = resolve_upstream_public_error(upstream, 402)
        check(f"upstream {upstream} -> {expected}", code == expected, code)


def test_envelopes() -> None:
    with TestClient(app) as c:
        # /v1 -> [OI] envelope, English message
        r = c.post("/v1/nai/generate-image", json={"bad": True}, headers=CLIENT)
        body = r.json()
        check("/v1 error uses the [OI] envelope",
              set(body.get("error", {})) == {"message", "type", "code"}, body)
        check("[OI] error type is derived from the status",
              body["error"]["type"] in {"invalid_request_error", "server_error"}, body)

        # /native -> native envelope with request_id, Chinese message
        r = c.post("/native/ai/generate-image", json={"bad": True}, headers=CLIENT)
        body = r.json()
        check("/native error uses the native envelope",
              set(body) == {"message", "code", "request_id"}, body)

        # /ai -> launcher envelope, no request_id
        r = c.post("/ai/generate-image", json={"bad": True}, headers=CLIENT)
        body = r.json()
        check("/ai error uses the launcher envelope", set(body) == {"message", "code"}, body)

        # admin -> platform envelope
        r = c.get("/admin/keys/does-not-exist/refresh-account", headers=ADMIN)
        body = r.json()
        check("admin error uses the platform envelope",
              set(body) == {"error"} and {"code", "message", "status_code"} <= set(body["error"]),
              body)


def test_shapes() -> None:
    with TestClient(app) as c:
        # ZIP entry naming: image_0.png, image_1.png
        from app.routes.responses import images_to_zip
        from PIL import Image

        archive_bytes = images_to_zip([Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8))])
        names = sorted(zipfile.ZipFile(io.BytesIO(archive_bytes)).namelist())
        check("ZIP entries are named image_0.png, image_1.png",
              names == ["image_0.png", "image_1.png"], names)

        # job_summary key set
        from app.generation import job_summary

        summary = job_summary({
            "job_id": "j", "request_id": "r", "model": "m", "action": "generate",
            "cost_gems": 1, "status": "succeeded",
            "pricing_snapshot": '{"request":{"width":832,"height":1216,"steps":28,"n_samples":1}}',
        })
        check("job summary has exactly YesNAI's keys",
              set(summary) == YESNAI_JOB_KEYS, set(summary) ^ YESNAI_JOB_KEYS)
        check("job summary reports dimensions from the snapshot",
              (summary["width"], summary["height"], summary["steps"], summary["n_samples"])
              == (832, 1216, 28, 1), summary)

        # subscription payload
        r = c.get("/user/subscription", headers=CLIENT)
        launcher = r.json()
        check("launcher subscription has YesNAI's keys",
              set(launcher) >= {"tier", "is_opus", "active", "expiresAt",
                                "trainingStepsLeft", "perks", "accountType",
                                "isGracePeriod", "currency"}, set(launcher))
        check("launcher subscription perks match YesNAI",
              set(launcher["perks"]) == YESNAI_PERKS_KEYS, set(launcher["perks"]))
        check("launcher subscription reports currency=gems",
              launcher["currency"] == "gems", launcher["currency"])
        check("launcher subscription accountType is an integer",
              isinstance(launcher["accountType"], int), launcher["accountType"])

        r = c.get("/native/user/subscription", headers=CLIENT)
        native = r.json()
        check("native subscription omits the currency extension",
              "currency" not in native, native)
        check("native subscription perks match YesNAI",
              set(native["perks"]) == YESNAI_PERKS_KEYS, set(native["perks"]))

        # tool validation, as YesNAI enforces it
        r = c.post("/ai/upscale", json={"image": "AAAA", "scale": 3}, headers=CLIENT)
        check("upscale rejects a scale other than 2 or 4",
              r.status_code == 400 and r.json()["code"] == "INVALID_NATIVE_REQUEST", r.text)

        r = c.post("/ai/augment-image", json={"image": "AAAA"}, headers=CLIENT)
        check("augment-image requires req_type",
              r.status_code == 400 and r.json()["code"] == "INVALID_NATIVE_REQUEST", r.text)

        r = c.post("/ai/upscale", json={"scale": 2}, headers=CLIENT)
        check("tools require an image",
              r.status_code == 400 and r.json()["code"] == "INVALID_NATIVE_REQUEST", r.text)

        # /v1/models is public, matching YesNAI
        r = c.get("/v1/models")
        check("/v1/models needs no credential", r.status_code == 200, r.status_code)

        # suggest-tags never errors, matching YesNAI
        r = c.get("/ai/generate-image/suggest-tags", params={"prompt": "a"}, headers=CLIENT)
        check("suggest-tags degrades to an empty list",
              r.status_code == 200 and r.json() == {"tags": []}, r.text)


def test_legacy_estimate_alignment() -> None:
    """The legacy estimate endpoints must use the authoritative estimator."""
    with TestClient(app) as c:
        payload = {
            "model": "nai-diffusion-3", "action": "generate", "input": "1girl",
            "parameters": {
                "width": 1472, "height": 1472, "steps": 50, "n_samples": 2,
                "sampler": "k_euler_ancestral", "scale": 5, "seed": 1,
            },
        }
        legacy = c.post("/v1/estimate/high-level", json=payload, headers=CLIENT).json()
        quote = c.post("/ai/generate-image/quote", json=payload, headers=CLIENT).json()

        check("legacy estimate keeps its historical keys",
              {"requested_model", "resolved_model", "max_anlas", "allowed",
               "is_opus_assumed", "estimated_anlas"} <= set(legacy), set(legacy))
        check("legacy estimate reports the same Anlas as the authoritative quote",
              legacy["estimated_anlas"]["total"] == quote["estimated_anlas"],
              (legacy["estimated_anlas"]["total"], quote["estimated_anlas"]))

        # The alias budget uses the YesNAI per-request limit code.
        aliased = dict(payload)
        aliased["model"] = "nai-diffusion-3-anlas-1"
        r = c.post("/ai/generate-image", json=aliased, headers=CLIENT)
        check("alias budget overrun uses TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED",
              r.status_code == 402 and r.json()["code"] == "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED",
              r.text)


def test_alias_consistency() -> None:
    """The `-anlas-N` alias must behave identically on every surface.

    It used to be parsed only on the legacy `/v1/generate/*` paths, so the same
    request elsewhere sent the alias itself upstream and failed with
    INVALID_PRICING_PARAMETER.
    """
    with TestClient(app) as c:
        base = {
            "action": "generate",
            "input": "1girl",
            "parameters": {
                "width": 1472, "height": 1472, "steps": 50, "n_samples": 2,
                "sampler": "k_euler_ancestral", "scale": 5, "seed": 1,
            },
        }
        surfaces = (
            ("/ai/generate-image/quote", "launcher"),
            ("/native/ai/generate-image", "native"),
            ("/v1/nai/generate-image", "ynai"),
        )
        for path, label in surfaces:
            payload = {**base, "model": "nai-diffusion-3-anlas-1"}
            r = c.post(path, json=payload, headers=CLIENT)
            check(f"alias resolves on the {label} surface",
                  r.status_code != 400 or "INVALID_PRICING_PARAMETER" not in r.text,
                  r.text[:160])

        # The alias must not leak upstream: a permissive alias prices normally.
        payload = {**base, "model": "nai-diffusion-3-anlas-9999"}
        r = c.post("/ai/generate-image/quote", json=payload, headers=CLIENT)
        check("a permissive alias prices the canonical model",
              r.status_code == 200 and r.json()["model"] == "nai-diffusion-3", r.text[:200])

        # An exceeded budget is rejected with the YesNAI limit code, on any surface.
        for path in ("/ai/generate-image", "/v1/nai/generate-image"):
            payload = {**base, "model": "nai-diffusion-3-anlas-1"}
            r = c.post(path, json=payload, headers=CLIENT)
            body = r.json()
            code = body.get("code") or (body.get("error") or {}).get("code")
            check(f"exceeded alias budget rejected on {path}",
                  r.status_code == 402 and code == "TOKEN_PER_REQUEST_GEMS_LIMIT_EXCEEDED",
                  r.text[:160])

        # The private alias keys must never reach the upstream payload.
        from app.generation import ALIAS_BUDGET_KEY, PRIVATE_PAYLOAD_KEYS, normalize_native_payload

        normalized = normalize_native_payload({**base, "model": "nai-diffusion-3-anlas-7"})
        check("normalization records the alias budget",
              normalized.get(ALIAS_BUDGET_KEY) == 7, normalized.get(ALIAS_BUDGET_KEY))
        check("normalization canonicalizes the model",
              normalized["model"] == "nai-diffusion-3", normalized["model"])
        check("private alias keys are declared",
              PRIVATE_PAYLOAD_KEYS == {ALIAS_BUDGET_KEY, "_bestnai_requested_model"},
              PRIVATE_PAYLOAD_KEYS)


def main() -> int:
    print("--- error contract ---")
    test_catalog()
    print("\n--- envelopes ---")
    test_envelopes()
    print("\n--- response shapes ---")
    test_shapes()
    print("\n--- legacy estimate alignment ---")
    test_legacy_estimate_alignment()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): " + ", ".join(failures))
        return 1
    print("all YesNAI contract checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
