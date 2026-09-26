# BestNAI Upload and Image Strategy

## 1. Supported input styles

BestNAI accepts images in three forms:

1. **Multipart upload**
   - `POST /v1/upload/image`
2. **Inline base64**
   - directly embedded in the JSON payload
3. **Upload ID reference**
   - `upload_id:<id>`

## 2. Recommended path

For real UI usage, the recommended path is:

1. User selects an image
2. Frontend uploads it to `/v1/upload/image`
3. Backend returns an `upload_id`
4. Frontend stores the ID
5. Generation payload uses `upload_id:<id>`

This keeps payloads smaller and avoids repeated base64 blobs.

## 3. Upload behavior

The upload endpoint:

- reads the raw file bytes
- validates that it can be parsed as an image
- converts / normalizes it to PNG internally
- computes SHA-256
- deduplicates in memory
- returns image metadata

## 4. `upload_id` resolution

Before validation, the backend recursively scans the request body and replaces:

- `upload_id:<id>`

with the stored base64 image.

This means every supported field can work with uploaded images:

- `i2i.image`
- `inpaint.image`
- `inpaint.mask`
- `character_references[].image`
- `controlnet.images[].image`

## 5. Output modes

### 5.1 JSON
Returns base64 strings in a JSON response.

### 5.2 Binary
For one-image responses, returns raw `image/png` or `image/webp` bytes.

### 5.3 ZIP
For multi-image responses, returns a ZIP with image files and metadata.

## 6. Suggested usage patterns

### Preview
- `return_format=json&image_format=webp`

### Download
- `return_format=zip&image_format=png`

### Fast single-image response
- `return_format=binary&image_format=webp`

## 7. Streaming and preview behavior

BestNAI supports SSE streaming for progress updates.

The frontend can use the stream endpoint to show:

- first visible chunk quickly
- image updates as generation progresses
- final result without waiting for a full JSON blob first

## 8. Inpaint / mask handling

For inpainting, the frontend is expected to send:

- source image
- mask image

Mask convention:
- white = repaint
- black = keep

The SDK will normalize the mask to its own required format and latent-grid alignment.

## 9. Practical advice for frontend authors

- always prefer upload IDs for repeated images
- keep raw base64 only as a fallback
- show file size and resolution after upload
- preserve the upload ID in the UI state so users can regenerate without re-uploading
