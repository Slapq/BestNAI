# BestNAI API 文档

This document is for frontend and integration developers.

## 1. General conventions

### Base URL
Typically the backend runs at:

```text
http://127.0.0.1:8010
```

### Auth
Most endpoints accept a client token through:

```http
Authorization: Bearer <client_secret>
```

BYOK generation additionally accepts:

```http
X-NAI-Key: pst-...
```

### Content types
- JSON: `application/json`
- Upload: `multipart/form-data`
- Streaming: `text/event-stream`

### Model alias convention
Use:

- `model-name-anlas-N`

Compatibility input:

- `model-name-points-N`

### Return format
Generation endpoints support:

- `return_format=json`  — default
- `return_format=binary` — one image as raw binary
- `return_format=zip` — multi-image ZIP

Image format:

- `image_format=png` — default
- `image_format=webp`

## 2. Health

### `GET /health`
Returns scheduler and queue status.

Example:

```json
{
  "total_keys": 1,
  "busy_keys": 0,
  "idle_keys": 1,
  "queued_requests": 0,
  "queue_timeout_seconds": 15.0
}
```

## 3. Upload image

### `POST /v1/upload/image`

`multipart/form-data`

Field:
- `file`: image file

Returns:

```json
{
  "upload_id": "6e35e4c2d632339a",
  "sha256": "...",
  "width": 1024,
  "height": 1024,
  "size_bytes": 12345,
  "mime_type": "image/png"
}
```

### Notes
- The backend stores a PNG-normalized version
- The returned `upload_id` can be used in later payloads as `upload_id:<id>`
- This works for i2i, inpaint, CR, and Vibe inputs

## 4. Estimation

### 4.1 High-level estimation
`POST /v1/estimate/high-level`

Use the same body as high-level generation.
The backend returns a detailed Anlas breakdown.

### 4.2 Low-level estimation
`POST /v1/estimate/low-level`

Native/SDK-friendly request model.

### 4.3 Native-compatible estimation
`POST /ai/estimate-image`

This mirrors the native image API shape.

### 4.4 BYOK estimation
`POST /v1/estimate/byok`

Same body as high-level estimation.
No `X-NAI-Key` is required because no upstream request is made.

## 5. Generation

### 5.1 High-level generation
`POST /v1/generate/high-level`

### 5.2 Low-level generation
`POST /v1/generate/low-level`

### 5.3 Native-compatible generation
`POST /ai/generate-image`

### 5.4 BYOK generation
`POST /v1/generate/byok`

Add user key:

```http
X-NAI-Key: pst-...
```

### Common query parameters
- `return_format=json|binary|zip`
- `image_format=png|webp`

### Response shape for JSON

```json
{
  "requested_model": "nai-diffusion-4-full",
  "resolved_model": "nai-diffusion-4-full",
  "max_anlas": -1,
  "estimated_anlas": {
    "total": 0
  },
  "image_format": "png",
  "images": ["<base64>"]
}
```

## 6. Streaming generation

### 6.1 High-level streaming
`POST /v1/generate/high-level-stream`

### 6.2 Low-level streaming
`POST /v1/generate/low-level-stream`

### 6.3 Native-compatible streaming
`POST /ai/generate-image-stream`

### 6.4 BYOK streaming
`POST /v1/generate/byok-stream`

### Fake-stream default
For streaming endpoints, BestNAI defaults to **fake-stream** behavior unless explicitly disabled.

- Default: `fake_stream=1`
- True non-stream: `fake_stream=0`

This means the server can return the first visible chunk sooner in the UI.

### SSE format
Every message is a `data:` line containing a JSON chunk.
Errors are emitted as:

```text
event: error
data: {...}
```

### Typical chunk fields
- `event_type`: `intermediate` or `final`
- `samp_ix`
- `step_ix`
- `gen_id`
- `sigma`
- `image`

## 7. Vibe encoding

### `POST /v1/encode-vibe`
### `POST /ai/encode-vibe`

Used to encode Vibe transfer images.

## 8. Admin

### `GET /admin/status`
Returns key pool and queue state.

## 9. Common payload fields

### Core fields
- `prompt`
- `model`
- `size`
- `steps`
- `scale`
- `sampler`
- `seed`
- `n_samples`
- `quality`
- `uc_preset`
- `noise_schedule`
- `cfg_rescale`
- `variety_boost`
- `image_format`

### Reference fields
- `character_references`
- `controlnet`
- `characters`

### Image transform fields
- `i2i`
- `inpaint`

## 10. Recommended frontend flow

1. Edit prompt and parameters
2. Auto-estimate Anlas
3. Upload images to get `upload_id`
4. Use `upload_id:<id>` in the generation payload
5. Choose JSON / binary / ZIP output as needed
6. Use stream endpoints for perceived speed
