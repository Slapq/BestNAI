# BestNAI OpenAPI Notes

This is not a formal generated OpenAPI spec.
It is a practical endpoint reference with response conventions.

## 1. Success responses

BestNAI may return one of these successful response styles:

- **JSON** — standard API responses
- **binary** — single image bytes
- **ZIP** — multiple images packaged as a zip file
- **SSE** — stream of chunks for progressive display

## 2. Error responses

All errors should follow the same envelope:

```json
{
  "error": {
    "code": "...",
    "message": "...",
    "status_code": 400,
    "details": {}
  }
}
```

## 3. Endpoint list

### Public / common
- `GET /health`
- `POST /v1/upload/image`
- `GET /admin/status`

### Estimation
- `POST /v1/estimate/high-level`
- `POST /v1/estimate/low-level`
- `POST /v1/estimate/byok`
- `POST /ai/estimate-image`

### Generation
- `POST /v1/generate/high-level`
- `POST /v1/generate/low-level`
- `POST /v1/generate/byok`
- `POST /ai/generate-image`

### Streaming
- `POST /v1/generate/high-level-stream`
- `POST /v1/generate/low-level-stream`
- `POST /v1/generate/byok-stream`
- `POST /ai/generate-image-stream`

### Vibe
- `POST /v1/encode-vibe`
- `POST /ai/encode-vibe`

## 4. Suggested response models

### Health

```json
{
  "total_keys": 1,
  "busy_keys": 0,
  "idle_keys": 1,
  "queued_requests": 0,
  "queue_timeout_seconds": 15.0
}
```

### Upload result

```json
{
  "upload_id": "...",
  "sha256": "...",
  "width": 1024,
  "height": 1024,
  "size_bytes": 123456,
  "mime_type": "image/png"
}
```

### Estimate result

```json
{
  "requested_model": "...",
  "resolved_model": "...",
  "max_anlas": -1,
  "allowed": true,
  "is_opus_assumed": true,
  "estimated_anlas": { "total": 0 }
}
```

### Generate result

```json
{
  "requested_model": "...",
  "resolved_model": "...",
  "max_anlas": -1,
  "estimated_anlas": { "total": 0 },
  "image_format": "png",
  "images": ["<base64>"]
}
```

## 5. Streaming chunk shape

Typical chunk fields:

- `event_type`: `intermediate` or `final`
- `samp_ix`
- `step_ix`
- `gen_id`
- `sigma`
- `image`

## 6. Fake-stream mode

Stream endpoints default to fake-stream mode unless explicitly disabled.

- `fake_stream=1` — collect first, then emit SSE-like chunks
- `fake_stream=0` — true upstream stream

If you want the frontend to feel faster, keep the default enabled.
If you want exact upstream-like streaming, disable it explicitly.

## 7. Compatibility note

The gateway still aims to mirror the official NovelAI payload style as closely as practical.
Where the gateway adds platform-specific logic, that logic is documented in the architecture and API docs.
