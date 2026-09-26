"""End-to-end test: YesNAI SDK -> BestNAI gateway -> mock NovelAI.

Starts two real HTTP servers (the mock upstream and BestNAI), then drives the
gateway exclusively through the published SDK.  No real NovelAI credentials or
paid generation are involved.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SDK_SRC = REPO_ROOT / "sdk" / "src"
sys.path.insert(0, str(SDK_SRC))

MOCK_PORT = 18901
GATEWAY_PORT = 18010
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}"
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"

CLIENT_SECRET = "e2e-client-secret"
ADMIN_SECRET = "e2e-admin-secret"
UPSTREAM_KEY = "pst-" + "e" * 40

failures: list[str] = []
children: list[subprocess.Popen] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else f"  <- {detail}"))
    if not ok:
        failures.append(label)


def wait_for_port(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


LOG_DIR: Path | None = None


def start_server(name: str, target: str, port: int, env: dict[str, str]) -> subprocess.Popen:
    """Launch `uvicorn <target>` as a child, logging to a file.

    Output goes to a file rather than a pipe so a chatty child can never block
    on a full pipe.
    """
    assert LOG_DIR is not None
    log_path = LOG_DIR / f"{name}.log"
    log_handle = open(log_path, "wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import uvicorn; uvicorn.run({target!r}, host='127.0.0.1', port={port}, log_level='warning')",
        ],
        cwd=str(REPO_ROOT),
        env={**os.environ, **env},
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    process._bestnai_log = log_handle  # type: ignore[attr-defined]
    process._bestnai_log_path = log_path  # type: ignore[attr-defined]
    children.append(process)
    return process


def read_child_log(process: subprocess.Popen) -> str:
    handle = getattr(process, "_bestnai_log", None)
    path = getattr(process, "_bestnai_log_path", None)
    if handle is not None:
        handle.flush()
    if path is None:
        return ""
    return Path(path).read_text(encoding="utf-8", errors="replace")[-4000:]


def stop_children() -> None:
    for process in children:
        process.terminate()
    for process in children:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    for process in children:
        handle = getattr(process, "_bestnai_log", None)
        if handle is not None:
            handle.close()
    children.clear()


def post_json(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=__import__("json").dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return __import__("json").loads(response.read())


def main() -> int:
    global LOG_DIR
    workspace = Path(tempfile.mkdtemp(prefix="bestnai-e2e-"))
    LOG_DIR = workspace
    db_path = workspace / "bestnai.db"

    # ── Start the mock upstream ──────────────────────────────────────────────
    mock_process = start_server(
        "mock-novelai", "tests.mock_novelai:app", MOCK_PORT, {"PYTHONPATH": str(REPO_ROOT)}
    )
    if not wait_for_port(MOCK_PORT):
        print("--- mock upstream output ---")
        print(read_child_log(mock_process))
        check("mock upstream listening", False, f"port {MOCK_PORT}")
        stop_children()
        return 1
    check("mock upstream listening", True)

    # ── Start BestNAI pointed at the mock ────────────────────────────────────
    gateway_env = {
        "PYTHONPATH": str(REPO_ROOT),
        "BESTNAI_DB_PATH": str(db_path),
        "BESTNAI_HOST": "127.0.0.1",
        "BESTNAI_PORT": str(GATEWAY_PORT),
        "BESTNAI_REQUIRE_AUTH": "true",
        "BESTNAI_CLIENT_SECRETS": CLIENT_SECRET,
        "BESTNAI_ADMIN_SECRETS": ADMIN_SECRET,
        "BESTNAI_API_KEYS": "",
        "BESTNAI_DYNAMIC_POOL": "true",
        "BESTNAI_NOVELAI_IMAGE_BASE": MOCK_URL,
        "BESTNAI_NOVELAI_API_BASE": MOCK_URL,
        "BESTNAI_QUEUE_TIMEOUT": "3",
    }
    gateway_process = start_server("gateway", "app.main:app", GATEWAY_PORT, gateway_env)
    gateway_ready = wait_for_port(GATEWAY_PORT)
    if not gateway_ready:
        print("--- gateway output ---")
        print(read_child_log(gateway_process))
        print("--- end gateway output ---")
    check("gateway listening", gateway_ready, f"port {GATEWAY_PORT}")
    if not gateway_ready:
        stop_children()
        return 1

    try:
        run_checks()
    finally:
        stop_children()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): " + ", ".join(failures))
        return 1
    print("all end-to-end checks passed")
    return 0


def run_checks() -> None:
    import json

    from yesnai import AsyncYesNAI, YesNAI
    from yesnai.exceptions import (
        InsufficientBalanceError,
        InvalidRequestError,
        QueueUnavailableError,
    )
    from yesnai.types import (
        AnnotateParams,
        AugmentParams,
        Character,
        CharacterReference,
        ControlNet,
        ControlNetImage,
        EncodeVibeParams,
        GenerateImageParams,
        I2iParams,
        InpaintParams,
        UpscaleParams,
    )
    from yesnai.types.api.image import ImageGenerationRequest, ImageParameters

    admin_headers = {"Authorization": f"Bearer {ADMIN_SECRET}"}
    import httpx

    admin = httpx.Client(base_url=GATEWAY_URL, headers=admin_headers, timeout=30)

    # ── Health and catalogue through the SDK ─────────────────────────────────
    client = YesNAI(api_key=CLIENT_SECRET, base_url=GATEWAY_URL, timeout=60)

    health = client.account.health()
    check("SDK reads /health", health.get("status") == "ok", health)

    models = client.models.list()
    check("SDK lists models", "nai-diffusion-4-5-full" in models and len(models) == 16, models)

    pricing = client.models.public_pricing()
    check("SDK reads public pricing", "models" in pricing and "rules" in pricing, list(pricing))

    # ── Pool starts empty: the queue error is the documented 503 ─────────────
    try:
        client.image.generate(GenerateImageParams(prompt="1girl"))
        check("empty pool raises QueueUnavailableError", False, "no error raised")
    except QueueUnavailableError as exc:
        check("empty pool raises QueueUnavailableError",
              exc.status_code == 503 and exc.code == "UPSTREAM_UNAVAILABLE", exc)

    # ── Add a key through the admin API (SDK has no admin surface) ───────────
    created = admin.post("/admin/keys", json={"api_key": UPSTREAM_KEY, "note": "e2e"})
    check("admin adds an upstream key", created.status_code == 201, created.text)
    key_id = created.json()["key"]["key_id"]

    account = created.json()["key"]
    check("key account information is recorded",
          account["total_anlas"] == 3500 and account["subscription_tier"] == 3, account)

    health = client.account.health()
    check("pool now reports one key", health["total_keys"] == 1 and health["idle_keys"] == 1, health)

    subscription = client.account.subscription()
    check("SDK reads launcher subscription", subscription["currency"] == "gems", subscription)
    native_subscription = client.account.native_subscription()
    check("SDK reads native subscription", "currency" not in native_subscription, native_subscription)

    # ── Quote before generating ──────────────────────────────────────────────
    params = GenerateImageParams(
        prompt="1girl, masterpiece", model="nai-diffusion-4-5-full", size="portrait", steps=28
    )
    quote = client.image.quote(params)
    check("quote returns a price without charging",
          "total_gems" in quote and quote["model"] == "nai-diffusion-4-5-full", quote)

    # ── Local Anlas estimate agrees with the gateway's formula ───────────────
    local = client.image.estimate(params)
    check("local Anlas estimate is positive", local.estimated_anlas > 0, local.as_dict())

    # ── Non-streaming generation (ZIP) ───────────────────────────────────────
    result = client.image.generate(params)
    check("SDK generate returns an image", len(result.images) == 1, len(result.images))
    check("generated image decodes as PNG", result.images[0].size == (64, 64), result.images[0].size)
    check("Gems charged header is exposed", result.gems_charged is not None, result.gems_charged)
    check("generation metadata carries a job id", bool(result.metadata.get("job_id")), result.metadata)

    # ── JSON/base64 surface ─────────────────────────────────────────────────
    json_result = client.image.generate_json(params)
    check("SDK generate_json returns base64 images",
          len(json_result.images) == 1 and json_result.image_format == "png", json_result.job)

    # ── [OI] Images surface ─────────────────────────────────────────────────
    openai_result = client.image.generate_openai(params)
    check("SDK generate_openai works", len(openai_result.images) == 1, openai_result.job)

    # ── Native surface ──────────────────────────────────────────────────────
    native_result = client.image.generate(params, native=True)
    check("SDK native generate works", len(native_result.images) == 1, native_result.metadata)

    # ── Low-level request passthrough ────────────────────────────────────────
    low_level = ImageGenerationRequest(
        model="nai-diffusion-3",
        action="generate",
        input="1girl",
        parameters=ImageParameters(width=832, height=1216, steps=28, sampler="k_euler_ancestral"),
    )
    low_result = client.image.generate(low_level, native=True)
    check("low-level request is forwarded unchanged", len(low_result.images) == 1, low_result.metadata)

    # ── Streaming (MessagePack frames) ───────────────────────────────────────
    events = list(client.image.stream(params))
    kinds = [event.event_type for event in events]
    check("SDK decodes stream frames", kinds == ["intermediate", "intermediate", "final"], kinds)
    final = [event for event in events if event.event_type == "final"]
    check("final stream frame carries an image", bool(final and final[0].image), final)

    native_events = list(client.image.stream(params, native=True))
    check("SDK decodes the native stream", [e.event_type for e in native_events][-1] == "final",
          [e.event_type for e in native_events])

    # ── Features: i2i and characters reach upstream ─────────────────────────
    from PIL import Image as PILImage

    source = PILImage.new("RGB", (64, 64), "blue")
    i2i_params = GenerateImageParams(
        prompt="1girl", model="nai-diffusion-4-5-full", size="portrait",
        i2i=I2iParams(image=source, strength=0.6),
    )
    i2i_quote = client.image.quote(i2i_params)
    check("i2i quote reports the i2i feature",
          i2i_quote["features"]["i2i"] is True and i2i_quote["applied_action_strength"] == 0.6, i2i_quote)
    check("i2i generation succeeds", len(client.image.generate(i2i_params).images) == 1)

    char_params = GenerateImageParams(
        prompt="1girl", model="nai-diffusion-4-5-full", size="portrait",
        characters=[Character(prompt="girl", position=(0.3, 0.5))],
    )
    check("character prompts are accepted", len(client.image.generate(char_params).images) == 1)

    vibe_params = GenerateImageParams(
        prompt="1girl", model="nai-diffusion-4-5-full", size="portrait",
        controlnet=ControlNet(images=[ControlNetImage(image=source)]),
    )
    check("vibe transfer is accepted", len(client.image.generate(vibe_params).images) == 1)

    reference_params = GenerateImageParams(
        prompt="1girl", model="nai-diffusion-4-5-full", size="portrait",
        character_references=[CharacterReference(image=source, type="character", strength=0.5)],
    )
    check("precise reference is accepted", len(client.image.generate(reference_params).images) == 1)

    inpaint_params = GenerateImageParams(
        prompt="1girl", model="nai-diffusion-4-5-full", size="portrait",
        inpaint=InpaintParams(image=source, mask=source, strength=0.8),
    )
    inpaint_quote = client.image.quote(inpaint_params)
    check("inpaint quote reports the inpaint feature",
          inpaint_quote["features"]["inpaint"] is True, inpaint_quote["features"])
    check("inpaint generation succeeds", len(client.image.generate(inpaint_params).images) == 1)

    # ── Tools ───────────────────────────────────────────────────────────────
    encoded = client.image.encode_vibe(EncodeVibeParams(image=source))
    check("SDK encode_vibe returns data", bool(encoded), encoded)

    upscaled = client.image.upscale(UpscaleParams(image=source, scale=2))
    check("SDK upscale works", len(upscaled.images) == 1, upscaled.metadata)

    augmented = client.image.augment(AugmentParams(image=source, req_type="bg-removal"))
    check("SDK augment works", len(augmented.images) == 1)

    annotated = client.image.annotate(AnnotateParams(image=source, req_type="wd-tagger"))
    check("SDK annotate works", annotated.status_code == 200, annotated.status_code)

    tags = client.image.suggest_tags("1gi", model="nai-diffusion-4-5-full")
    check("SDK suggest_tags returns tags", len(tags) == 2, tags)

    # ── Upload + upload_id reuse ────────────────────────────────────────────
    import io

    buffer = io.BytesIO()
    source.save(buffer, format="PNG")
    uploaded = client.upload.image(buffer.getvalue())
    check("SDK upload returns an upload_id", bool(uploaded.upload_id), uploaded)

    ref_params = GenerateImageParams(
        prompt="1girl", model="nai-diffusion-4-5-full", size="portrait",
        i2i=I2iParams(image=uploaded.reference, strength=0.6),
    )
    check("upload_id reference round-trips", len(client.image.generate(ref_params).images) == 1)

    # ── Idempotency ─────────────────────────────────────────────────────────
    from yesnai.exceptions import ConflictError

    first = client.image.generate(params, idempotency_key="e2e-1")
    check("first idempotent generation succeeds", len(first.images) == 1)
    try:
        client.image.generate(params, idempotency_key="e2e-1")
        check("repeated idempotency key is rejected", False, "no error raised")
    except ConflictError as exc:
        check("repeated idempotency key is rejected",
              exc.code == "DUPLICATE_GENERATION_REQUEST", exc)

    try:
        client.image.generate(
            GenerateImageParams(prompt="different prompt", model="nai-diffusion-4-5-full", size="portrait"),
            idempotency_key="e2e-1",
        )
        check("idempotency key reuse with a different payload is rejected", False, "no error raised")
    except ConflictError as exc:
        check("idempotency key reuse with a different payload is rejected",
              exc.code == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD", exc)

    # ── Validation errors ───────────────────────────────────────────────────
    try:
        client.image.generate(GenerateImageParams(prompt="1girl", size=(3000, 3000)))
        check("oversized request is rejected locally", False, "no error raised")
    except InvalidRequestError as exc:
        check("oversized request is rejected locally",
              "2048" in str(exc) and exc.status_code is None, exc)

    # The gateway enforces the same bound independently of the SDK.
    try:
        client.api_client.request(
            "POST",
            "/ai/generate-image/quote",
            json_body={
                "model": "nai-diffusion-3",
                "action": "generate",
                "input": "1girl",
                "parameters": {"width": 3000, "height": 3000, "steps": 28, "n_samples": 1},
            },
        )
        check("gateway rejects an oversized request", False, "no error raised")
    except InvalidRequestError as exc:
        check("gateway rejects an oversized request", exc.status_code == 400, exc)

    # ── Upstream failure mapping ────────────────────────────────────────────
    post_json(f"{MOCK_URL}/__fail", {"path": "/ai/generate-image", "status": 402,
                                     "body": {"message": "insufficient anlas"}})
    try:
        client.image.generate(params)
        check("upstream 402 maps to InsufficientBalanceError", False, "no error raised")
    except InsufficientBalanceError as exc:
        check("upstream 402 maps to InsufficientBalanceError",
              exc.status_code == 402 and exc.code == "INSUFFICIENT_GEMS", exc)
    except QueueUnavailableError as exc:
        check("upstream 402 maps to InsufficientBalanceError", False, f"got QueueUnavailableError: {exc}")
    post_json(f"{MOCK_URL}/__fail", {"path": "/ai/generate-image", "clear": True})

    # A rejected upstream credential is a server-side failure, not the caller's.
    post_json(f"{MOCK_URL}/__fail", {"path": "/ai/generate-image", "status": 401,
                                     "body": {"message": "unauthorized"}})
    try:
        client.image.generate(params)
        check("upstream 401 maps to a service error", False, "no error raised")
    except QueueUnavailableError as exc:
        check("upstream 401 maps to a service error",
              exc.status_code == 503 and exc.code == "UPSTREAM_UNAVAILABLE", exc)
    post_json(f"{MOCK_URL}/__fail", {"path": "/ai/generate-image", "clear": True})

    check("generation still works after an upstream failure",
          len(client.image.generate(params).images) == 1)

    # ── Async client parity ────────────────────────────────────────────────
    import asyncio

    async def async_checks() -> None:
        async with AsyncYesNAI(api_key=CLIENT_SECRET, base_url=GATEWAY_URL, timeout=60) as aclient:
            ahealth = await aclient.account.health()
            check("async SDK reads /health", ahealth.get("status") == "ok", ahealth)

            amodels = await aclient.models.list()
            check("async SDK lists models", len(amodels) == 16, amodels)

            aresult = await aclient.image.generate(params)
            check("async SDK generate works", len(aresult.images) == 1)

            ajson = await aclient.image.generate_json(params)
            check("async SDK generate_json works", len(ajson.images) == 1)

            aopenai = await aclient.image.generate_openai(params)
            check("async SDK generate_openai works", len(aopenai.images) == 1)

            aevents = [event async for event in aclient.image.stream(params)]
            check("async SDK decodes stream frames",
                  [e.event_type for e in aevents][-1] == "final", [e.event_type for e in aevents])

            aquote = await aclient.image.quote(params)
            check("async SDK quote works", "total_gems" in aquote, aquote)

            aupload = await aclient.upload.image(buffer.getvalue())
            check("async SDK upload works", bool(aupload.upload_id))

    asyncio.run(async_checks())

    # ── Key pool administration ────────────────────────────────────────────
    disabled = admin.post(f"/admin/keys/{key_id}/enabled", params={"enabled": "false"})
    check("admin disables the key", disabled.status_code == 200, disabled.text)
    try:
        client.image.generate(params)
        check("disabled key yields the queue error", False, "no error raised")
    except QueueUnavailableError as exc:
        check("disabled key yields the queue error", exc.status_code == 503, exc)

    enabled = admin.post(f"/admin/keys/{key_id}/enabled", params={"enabled": "true"})
    check("admin re-enables the key", enabled.status_code == 200, enabled.text)
    check("generation resumes", len(client.image.generate(params).images) == 1)

    keys = admin.get("/admin/keys").json()
    check("admin lists the key with account info",
          len(keys["keys"]) == 1 and keys["keys"][0]["subscription_tier_name"] == "Opus", keys["keys"])

    refreshed = admin.post(f"/admin/keys/{key_id}/refresh-account")
    check("admin refreshes account info", refreshed.status_code == 200, refreshed.text)

    deleted = admin.delete(f"/admin/keys/{key_id}")
    check("admin deletes the key", deleted.status_code == 200 and deleted.json()["deleted"] == 1, deleted.text)
    check("pool is empty again", client.account.health()["total_keys"] == 0)

    # ── Auth enforcement ───────────────────────────────────────────────────
    from yesnai.exceptions import AuthenticationError, ForbiddenError

    # `/v1/models` is intentionally public; an authenticated route must reject.
    check("model catalogue is public", client.models.list() != [])
    try:
        YesNAI(api_key="wrong-secret", base_url=GATEWAY_URL).account.subscription()
        check("wrong client credential is rejected", False, "no error raised")
    except AuthenticationError as exc:
        check("wrong client credential is rejected", exc.status_code == 401, exc)

    forbidden = httpx.get(
        f"{GATEWAY_URL}/admin/status",
        headers={"Authorization": f"Bearer {CLIENT_SECRET}"},
    )
    check("client credential cannot reach admin routes", forbidden.status_code == 403, forbidden.text)

    unauthenticated = httpx.get(f"{GATEWAY_URL}/user/subscription")
    check("missing credential is rejected", unauthenticated.status_code == 401, unauthenticated.text)

    client.close()
    admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
