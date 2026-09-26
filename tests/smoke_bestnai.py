"""BestNAI end-to-end smoke checks (no network, no real NovelAI keys)."""
import os
import tempfile

os.environ["BESTNAI_DYNAMIC_POOL"] = "true"
os.environ["BESTNAI_REQUIRE_AUTH"] = "false"
os.environ["BESTNAI_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["BESTNAI_ADMIN_SECRETS"] = "admin-secret"
os.environ["BESTNAI_CLIENT_SECRETS"] = "client-secret"
os.environ["BESTNAI_API_KEYS"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN = {"Authorization": "Bearer admin-secret"}
CLIENT = {"Authorization": "Bearer client-secret"}

V3 = {
    "model": "nai-diffusion-3",
    "action": "generate",
    "input": "1girl",
    "parameters": {
        "width": 832, "height": 1216, "steps": 28, "n_samples": 1,
        "sampler": "k_euler_ancestral", "scale": 5, "seed": 1,
    },
}
# Large request that cannot fit the free boundary -> Anlas formula
BIG = {
    "model": "nai-diffusion-3",
    "action": "generate",
    "input": "1girl",
    "parameters": {
        "width": 1472, "height": 1472, "steps": 50, "n_samples": 2,
        "sampler": "k_euler_ancestral", "scale": 5, "seed": 1,
    },
}

failures = []


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"  <- {detail}"))
    if not ok:
        failures.append(label)


with TestClient(app) as c:
    r = c.get("/health")
    check("health 200", r.status_code == 200, r.text)
    check("health reports pool counters", "queued_requests" in r.json(), r.text)

    r = c.get("/ready")
    check("ready 200", r.status_code == 200, r.text)

    # ── Model catalogue ──────────────────────────────────────────────────────
    r = c.get("/v1/models")
    check("/v1/models 200 with 16 models", r.status_code == 200 and len(r.json()["data"]) == 16, r.text)

    # ── Admin: seeded built-in rules ─────────────────────────────────────────
    r = c.get("/admin/pricing/rules", headers=ADMIN)
    buckets = {row["size_bucket"]: row for row in r.json()["rules"]}
    check("built-in anlas fallback seeded",
          "system-anlas-fallback" in buckets and buckets["system-anlas-fallback"]["pricing_type"] == "anlas_formula", r.text)
    check("built-in free standard seeded",
          "free-standard" in buckets and buckets["free-standard"]["pricing_type"] == "free_package", r.text)

    # ── Pricing: free boundary vs Anlas formula ──────────────────────────────
    r = c.post("/admin/pricing/rules/test-quote", json=V3, headers=ADMIN)
    q = r.json()["breakdown"]
    check("small V3 request priced by free package",
          r.status_code == 200 and q["pricing_mode"] == "gems_flat" and q["is_free_package"] and q["total_gems"] == 0, r.text)

    r = c.post("/admin/pricing/rules/test-quote", json=BIG, headers=ADMIN)
    q = r.json()["breakdown"]
    check("large V3 request priced by Anlas formula",
          r.status_code == 200 and q["pricing_mode"] == "anlas_formula", r.text)
    check("Anlas estimate is produced", int(q.get("estimated_anlas") or 0) > 0, r.text)
    check("Anlas formula reports the ratio", q.get("anlas_to_gems_ratio") == 1.0, r.text)

    # ── NAI5 multiplier (x1.5) ───────────────────────────────────────────────
    nai5 = dict(BIG)
    nai5["model"] = "nai-diffusion-5-full"
    r = c.post("/admin/pricing/rules/test-quote", json=nai5, headers=ADMIN)
    anlas5 = int(r.json()["breakdown"]["estimated_anlas"])
    r2 = c.post("/admin/pricing/rules/test-quote", json=BIG, headers=ADMIN)
    anlas3 = int(r2.json()["breakdown"]["estimated_anlas"])
    check("NAI5 carries the x1.5 Anlas multiplier", anlas5 > anlas3, f"nai5={anlas5} v3={anlas3}")

    # ── Custom pricing rule (flat_gems + multipliers) ────────────────────────
    r = c.post("/admin/pricing/rules", headers=ADMIN, json={
        "model": "nai-diffusion-3", "action": "*", "size_bucket": "custom-flat",
        "min_pixels": 0, "max_pixels": 3145728, "min_steps": 1, "max_steps": 50,
        "min_images": 1, "max_images": 8, "base_gems": 10, "priority": 50,
        "pricing_type": "flat_gems",
        "i2i_multiplier": 2.0, "inpaint_multiplier": 3.0,
        "allow_i2i": True, "allow_inpaint": True,
    })
    check("custom flat rule created", r.status_code == 201, r.text)
    rule_id = r.json()["rule"]["id"]

    r = c.post("/admin/pricing/rules/test-quote", json=BIG, headers=ADMIN)
    q = r.json()["breakdown"]
    check("flat rule charges base x samples",
          q["pricing_mode"] == "gems_flat" and q["total_gems"] == 20, f"total={q['total_gems']}")

    # i2i applies the multiplier and the raw strength
    i2i = {
        "model": "nai-diffusion-3", "action": "img2img", "input": "1girl",
        "parameters": {
            "width": 832, "height": 1216, "steps": 28, "n_samples": 1,
            "sampler": "k_euler_ancestral", "scale": 5, "seed": 1,
            "image": "AAAA", "strength": 0.5,
        },
    }
    r = c.post("/admin/pricing/rules/test-quote", json=i2i, headers=ADMIN)
    q = r.json()["breakdown"]
    check("i2i applies multiplier x strength",
          q["total_gems"] == 10 and q["applied_i2i_multiplier"] == 2.0 and q["applied_action_strength"] == 0.5,
          f"total={q['total_gems']} mult={q['applied_i2i_multiplier']} strength={q['applied_action_strength']}")

    # Feature gating: a zero-base rule cannot enable paid features
    r = c.post("/admin/pricing/rules", headers=ADMIN, json={
        "model": "nai-diffusion-3", "action": "generate", "size_bucket": "bad",
        "min_pixels": 0, "max_pixels": 3145728, "min_steps": 1, "max_steps": 50,
        "min_images": 1, "max_images": 8, "base_gems": 0, "priority": 60,
        "pricing_type": "flat_gems", "allow_i2i": True,
    })
    check("zero-base rule with paid feature rejected", r.status_code == 422, r.text)

    r = c.delete(f"/admin/pricing/rules/{rule_id}", headers=ADMIN)
    check("custom rule deleted", r.status_code == 200, r.text)

    # ── Quote surfaces ───────────────────────────────────────────────────────
    r = c.post("/ai/generate-image/quote", json=BIG, headers=CLIENT)
    check("launcher quote 200", r.status_code == 200 and "total_gems" in r.json(), r.text)

    r = c.post("/v1/nai/request-price", json=nai5, headers=CLIENT)
    check("/v1/nai/request-price returns a local estimate",
          r.status_code == 200 and r.json()["data"]["estimate_source"] == "BestNAI", r.text)

    r = c.post("/v1/estimate", json=BIG)
    check("/v1/estimate works without auth", r.status_code == 200 and "total_gems" in r.json(), r.text)

    r = c.get("/v1/pricing/public")
    check("/v1/pricing/public 200", r.status_code == 200 and "models" in r.json(), r.text)

    # ── Queue error: no keys configured -> 503 UPSTREAM_UNAVAILABLE ──────────
    r = c.post("/ai/generate-image", json=BIG, headers=CLIENT)
    check("launcher queue error is 503 UPSTREAM_UNAVAILABLE",
          r.status_code == 503 and r.json()["code"] == "UPSTREAM_UNAVAILABLE", r.text)

    r = c.post("/native/ai/generate-image", json=BIG, headers=CLIENT)
    body = r.json()
    check("native queue error uses the native envelope",
          r.status_code == 503 and body["code"] == "UPSTREAM_UNAVAILABLE" and "request_id" in body, r.text)

    r = c.post("/v1/nai/generate-image", json=BIG, headers=CLIENT)
    body = r.json()
    check("[OI] queue error uses the [OI] envelope",
          r.status_code == 503 and body["error"]["code"] == "UPSTREAM_UNAVAILABLE", r.text)

    # ── Idempotency ──────────────────────────────────────────────────────────
    hdr = {**CLIENT, "Idempotency-Key": "k1"}
    r1 = c.post("/v1/nai/generate-image", json=BIG, headers=hdr)
    check("first idempotent request reaches the queue error", r1.status_code == 503, r1.text)

    # The failed job is retained and settled, never left `charged`.
    r2 = c.post("/v1/nai/generate-image", json=BIG, headers=hdr)
    body = r2.json()["error"]
    check("failed job settles to `failed` and the key is retained",
          r2.status_code == 409
          and body["code"] == "DUPLICATE_GENERATION_REQUEST"
          and body["message"].endswith(":failed"), r2.text)

    # Same key, different payload -> distinct conflict code.
    other = dict(BIG)
    other["input"] = "2girls"
    r3 = c.post("/v1/nai/generate-image", json=other, headers=hdr)
    check("same key with a different payload is rejected distinctly",
          r3.status_code == 409
          and r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD", r3.text)

    r = c.post("/v1/nai/generate-image", json=BIG, headers={**CLIENT, "Idempotency-Key": "x" * 200})
    check("over-long idempotency key rejected",
          r.status_code == 400 and r.json()["error"]["code"] == "INVALID_NATIVE_REQUEST", r.text)

    # ── Request validation ───────────────────────────────────────────────────
    bad = dict(BIG)
    bad["parameters"] = {**BIG["parameters"], "width": 3000, "height": 3000}
    r = c.post("/ai/generate-image/quote", json=bad, headers=CLIENT)
    check("oversized image rejected", r.status_code == 400, r.text)

    bad2 = {"model": "nai-diffusion-3", "prompt": "1girl", "size": "832x1216"}
    r = c.post("/ai/generate-image/quote", json=bad2, headers=CLIENT)
    check("high-level convenience payload accepted", r.status_code == 200, r.text)

    # ── Admin: key pool CRUD (no upstream call needed) ───────────────────────
    r = c.get("/admin/keys", headers=ADMIN)
    check("admin key list 200", r.status_code == 200 and r.json()["keys"] == [], r.text)

    r = c.post("/admin/keys", headers=ADMIN, json={"api_key": "not-a-key"})
    check("invalid key format rejected", r.status_code == 400, r.text)

    r = c.get("/admin/options", headers=ADMIN)
    check("site options readable", r.status_code == 200 and "defaults" in r.json(), r.text)

    r = c.put("/admin/options", headers=ADMIN, json={"UnknownKey": 1})
    check("unknown option rejected", r.status_code == 422, r.text)

    r = c.put("/admin/options", headers=ADMIN, json={"ImagePricingAnlasRatio": 2.5})
    check("anlas ratio updated", r.status_code == 200 and r.json()["options"]["ImagePricingAnlasRatio"] == 2.5, r.text)

    r = c.post("/admin/pricing/rules/test-quote", json=BIG, headers=ADMIN)
    check("ratio scales the Gems price",
          r.json()["breakdown"]["total_gems"] == int(r.json()["breakdown"]["estimated_anlas"] * 2.5), r.text)

    # ── Auth enforcement ─────────────────────────────────────────────────────
    r = c.get("/admin/status")
    check("admin without credentials rejected", r.status_code == 401, r.text)
    r = c.get("/admin/status", headers=CLIENT)
    check("client credential rejected by admin", r.status_code == 403, r.text)

    # ── Subscription surfaces ────────────────────────────────────────────────
    r = c.get("/user/subscription", headers=CLIENT)
    check("launcher subscription 200 with currency", r.status_code == 200 and r.json()["currency"] == "gems", r.text)
    r = c.get("/native/user/subscription", headers=CLIENT)
    check("native subscription 200 without currency", r.status_code == 200 and "currency" not in r.json(), r.text)

    # ── Upload + upload_id resolution ────────────────────────────────────────
    from io import BytesIO
    from PIL import Image as PILImage

    buf = BytesIO()
    PILImage.new("RGB", (64, 64), "red").save(buf, format="PNG")
    r = c.post("/v1/upload/image", files={"file": ("a.png", buf.getvalue(), "image/png")}, headers=CLIENT)
    check("upload returns an upload_id", r.status_code == 200 and r.json()["upload_id"], r.text)
    uid = r.json()["upload_id"]

    i2i_ref = {
        "model": "nai-diffusion-3", "action": "img2img", "input": "1girl",
        "parameters": {
            "width": 832, "height": 1216, "steps": 28, "n_samples": 1,
            "sampler": "k_euler_ancestral", "scale": 5, "seed": 1,
            "image": f"upload_id:{uid}", "strength": 0.5,
        },
    }
    r = c.post("/ai/generate-image/quote", json=i2i_ref, headers=CLIENT)
    check("upload_id reference resolves in a quote", r.status_code == 200, r.text)

    bad_ref = dict(i2i_ref)
    bad_ref["parameters"] = {**i2i_ref["parameters"], "image": "upload_id:deadbeef"}
    r = c.post("/ai/generate-image/quote", json=bad_ref, headers=CLIENT)
    check("unknown upload_id rejected", r.status_code == 400, r.text)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): " + ", ".join(failures))
    raise SystemExit(1)
print("all checks passed")
