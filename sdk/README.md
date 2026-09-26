# YesNAI SDK

Python SDK for the **BestNAI** gateway. It mirrors the shape of the official
NovelAI SDK, so code written against `novelai` translates almost directly.

```text
your code  ->  yesnai SDK  ->  BestNAI gateway  ->  NovelAI
```

The SDK is published together with BestNAI from this repository.

## Install

From this repository:

```bash
pip install -e sdk
```

The package needs `httpx`, `pydantic`, `pillow`, and `msgpack`.

## Quick start

```python
from yesnai import YesNAI
from yesnai.types import GenerateImageParams

with YesNAI(api_key="ynai-...", base_url="http://127.0.0.1:8010") as client:
    params = GenerateImageParams(
        prompt="1girl, masterpiece, best quality",
        model="nai-diffusion-4-5-full",
        size="portrait",
        steps=28,
    )
    result = client.image.generate(params)
    result.images[0].save("out.png")
    print("Gems charged:", result.gems_charged)
```

`YESNAI_API_KEY` (or `BESTNAI_API_KEY`) is read from the environment when
`api_key` is omitted.

## Surfaces

Every gateway dialect is reachable, and each maps onto one method:

| Method | Gateway endpoint | Response |
|---|---|---|
| `image.generate(params)` | `POST /ai/generate-image` | ZIP of PNGs |
| `image.generate(params, native=True)` | `POST /native/ai/generate-image` | ZIP of PNGs |
| `image.generate_json(params)` | `POST /v1/nai/generate-image` | JSON + base64 |
| `image.generate_openai(params)` | `POST /v1/images/generations` | [OI] Images JSON |
| `image.stream(params)` | `POST /ai/generate-image-stream` | MessagePack frames |
| `image.stream(params, native=True)` | `POST /native/ai/generate-image-stream` | MessagePack frames |
| `image.quote(params)` | `POST /ai/generate-image/quote` | price, no charge |
| `image.request_price(params)` | `POST /v1/nai/request-price` | local estimate |
| `image.encode_vibe(...)` | `POST /ai/encode-vibe` | base64 |
| `image.upscale / augment / annotate` | `POST /ai/{operation}` | ZIP |
| `image.suggest_tags(prompt)` | `GET /ai/generate-image/suggest-tags` | tags |
| `account.subscription()` | `GET /user/subscription` | Launcher dialect |
| `account.native_subscription()` | `GET /native/user/subscription` | native dialect |
| `account.health()` | `GET /health` | pool and queue counters |
| `models.list()` | `GET /v1/models` | model IDs (public) |
| `models.public_pricing()` | `GET /v1/pricing/public` | rules (public) |
| `upload.image(bytes)` | `POST /v1/upload/image` | `upload_id` |

## High-level parameters

`GenerateImageParams` is the convenience form. The SDK applies quality tags and
the UC preset locally, then sends the native payload:

```python
from yesnai.types import (
    Character, CharacterReference, ControlNet, ControlNetImage,
    GenerateImageParams, I2iParams, InpaintParams,
)
from PIL import Image

source = Image.open("source.png")

# img2img
client.image.generate(GenerateImageParams(prompt="1girl", i2i=I2iParams(image=source, strength=0.6)))

# inpainting
client.image.generate(GenerateImageParams(prompt="1girl", inpaint=InpaintParams(image=source, mask=mask)))

# Vibe transfer
client.image.generate(GenerateImageParams(
    prompt="1girl",
    controlnet=ControlNet(images=[ControlNetImage(image=source, strength=0.6)]),
))

# Precise Reference
client.image.generate(GenerateImageParams(
    prompt="1girl",
    character_references=[CharacterReference(image=source, type="character", strength=0.5)],
))

# Multi-character
client.image.generate(GenerateImageParams(
    prompt="2girls",
    characters=[Character(prompt="girl A", position=(0.3, 0.5)),
                Character(prompt="girl B", position=(0.7, 0.5))],
))
```

## Low-level requests

Low-level requests are forwarded **without conversion**. Build them from the
mirrored REST models:

```python
from yesnai.types.api.image import ImageGenerationRequest, ImageParameters

request = ImageGenerationRequest(
    model="nai-diffusion-4-5-full",
    action="generate",
    input="1girl",
    parameters=ImageParameters(
        width=832, height=1216, steps=28,
        sampler="k_euler_ancestral", scale=5.0, seed=12345,
        negative_prompt="lowres", noise_schedule="karras",
        qualityToggle=True, ucPreset=1, params_version=3,
    ),
)
result = client.image.generate(request, native=True)
```

`ImageParameters` uses `extra="forbid"`: an unknown field is a caller mistake and
is reported rather than silently dropped.

## Streaming

`image.stream()` decodes the gateway's length-prefixed MessagePack frames:

```python
for chunk in client.image.stream(GenerateImageParams(prompt="1girl")):
    if chunk.event_type == "final":
        open("out.png", "wb").write(base64.b64decode(chunk.image))
```

The gateway relays upstream frames verbatim on `/native`, so a chunk boundary
never carries meaning — the SDK reassembles frames from a running buffer.

## Pricing

```python
quote = client.image.quote(params)          # gateway-computed, never charges
print(quote["total_gems"], quote["pricing_mode"])

estimate = client.image.estimate(params)    # local, no request
print(estimate.estimated_anlas)
```

> **Anlas values are estimates.** They come from a reverse-engineered formula used
> for platform pricing and routing. They are not an official NovelAI charge.

## Idempotency

Pass `idempotency_key` to make an operation retry-safe. Repeating the same key
with the same payload returns HTTP 409 `DUPLICATE_GENERATION_REQUEST`; reusing it
with a different payload returns `IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD`.

```python
client.image.generate(params, idempotency_key="order-42")
```

Use a new key to intentionally generate again.

## Errors

All errors derive from `YesNAIError` and carry a stable `code`:

| Exception | HTTP | Typical cause |
|---|---|---|
| `MissingAPIKeyError` | — | no credential supplied |
| `AuthenticationError` | 401 | bad credential |
| `ForbiddenError` | 403 | credential lacks permission |
| `InvalidRequestError` | 400 / 422 | bad parameters, no matching pricing rule |
| `NotFoundError` | 404 | unknown resource |
| `ConflictError` | 409 | idempotency conflict |
| `InsufficientBalanceError` | 402 | no upstream key can fund the request |
| `PayloadTooLargeError` | 413 | body or decoded image too large |
| `RateLimitError` | 429 | rate limited |
| `QueueUnavailableError` | 503 | **the queue error** — no key became available |
| `ServerError` | 5xx | gateway or upstream failure |
| `NetworkError` | — | gateway unreachable |

`QueueUnavailableError` is the queue signal: the request never reached NovelAI, so
retrying later is the correct response.

```python
from yesnai.exceptions import QueueUnavailableError

try:
    client.image.generate(params)
except QueueUnavailableError:
    time.sleep(5)   # the key pool was busy
```

## Async

```python
from yesnai import AsyncYesNAI

async with AsyncYesNAI(api_key="ynai-...") as client:
    result = await client.image.generate(params)
    async for chunk in client.image.stream(params):
        ...
```

## Notes and limitations

- The SDK does not expose administration endpoints. Key pool and pricing rule
  management belong to the operator API (`/admin/*`).
- `/v1/models` and `/v1/pricing/public` are public; everything else needs a
  credential.
- Local validation (for example an out-of-range image size) raises
  `InvalidRequestError` before any request is sent, so `status_code` is `None`.
