# BestNAI Architecture

This document explains how BestNAI is structured, how requests flow through the system,
and where each major capability lives.

## 1. High-level overview

BestNAI is a FastAPI-based gateway with three main responsibilities:

1. **Request normalization**
   - Normalize model aliases
   - Normalize upload references such as `upload_id:<id>`
   - Convert the frontend's friendly payload into NovelAI-compatible data

2. **Policy and platform logic**
   - Auth checks
   - Queue scheduling
   - Anlas estimation
   - Image upload storage and reuse
   - Response formatting

3. **User experience**
   - Host a single-page frontend
   - Provide real-time estimation
   - Provide streaming generation
   - Support BYOK and official-like UX

## 2. File map

### `app/main.py`
Main FastAPI app:
- route definitions
- exception handling
- streaming output
- upload endpoint
- frontend hosting
- BYOK endpoints

### `app/service.py`
Business logic:
- alias parsing
- image normalization
- Anlas estimation wrappers
- response breakdowns
- ZIP packing
- output encoding

### `app/image_store.py`
In-memory upload cache:
- SHA-256 deduplication
- `upload_id` lookup
- recursive payload resolution

### `app/scheduler.py`
Queue + lease management:
- FIFO queue
- single-key single-concurrency
- lease-based client acquisition

### `app/sdk_loader.py`
Finds the bundled NovelAI SDK source tree automatically.

### `frontend/index.html`
Single-page UI:
- prompt editor
- model and parameter controls
- CR / Vibe / i2i / inpaint / characters
- live estimation
- generation and streaming
- BYOK mode

## 3. Request flow

### 3.1 Standard generation flow

1. Frontend builds payload
2. Frontend calls `/v1/estimate/high-level`
3. Backend normalizes payload
4. Backend estimates Anlas using SDK logic
5. If allowed, frontend calls `/v1/generate/high-level`
6. Scheduler acquires a NovelAI key
7. Request is sent upstream
8. Output is returned as JSON / binary / ZIP / SSE

### 3.2 BYOK flow

1. Frontend enables BYOK toggle
2. Frontend sends `X-NAI-Key`
3. Backend uses the supplied key directly
4. No scheduler queue is used for the user key itself
5. Estimation still uses the gateway's estimate logic

### 3.3 Upload flow

1. Frontend uploads image via `/v1/upload/image`
2. Backend stores a PNG-normalized version in memory
3. Backend returns an `upload_id`
4. Later payloads can reference `upload_id:<id>`
5. Before validation, backend recursively resolves those references back to base64

## 4. Image handling model

BestNAI accepts images in three forms:

- direct base64 in JSON
- multipart upload
- `upload_id:<id>` references

All uploaded images are normalized and deduplicated.

## 5. Streaming model

BestNAI supports two stream styles:

### 5.1 Official-like SSE stream
- `text/event-stream`
- emits chunk JSON events
- used by the frontend for progressive display

### 5.2 Fake-stream mode
- default for stream endpoints
- backend first completes the generation
- then emits a SSE-like sequence from the completed result
- gives a much faster first visible response for many clients

This mode exists for perceived speed and UI responsiveness.

## 6. Why the architecture looks like this

BestNAI is intentionally not a thin proxy.
It acts as a gateway that adds:

- stable UX semantics
- cost estimation
- upload reuse
- queue control
- auth layers
- output packaging

At the same time, it tries to stay as close as possible to the official NovelAI API shape.

## 7. Important implementation notes

- SDK source is auto-detected by `app/sdk_loader.py`
- `-anlas-N` is the canonical alias format
- `-points-N` is accepted only as compatibility input
- Stream endpoints default to fake-stream behavior unless explicitly disabled
- `upload_id` storage is in-memory in the current version
- Image size validation follows SDK limits, not arbitrary platform limits

## 8. Future extension points

Recommended future work:

- persistent image storage
- audit logs and request IDs
- rate limiting
- memory caps for upload and response size
- history page in frontend
- richer SSE progress events
