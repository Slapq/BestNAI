# BestNAI Anlas Guide

This document explains the cost model exposed by the gateway.

## 1. Important principle

BestNAI does **not** invent its own billing engine.
It relies on the bundled NovelAI SDK's Anlas calculation logic and only wraps the result
into a more convenient response format.

That means:

- the gateway estimates rather than invoices
- values should be treated as platform-side estimates
- actual upstream behavior can change if NovelAI changes its own logic

## 2. Canonical alias format

Primary format:

- `model-name-anlas-N`

Compatibility input:

- `model-name-points-N`

The gateway normalizes the compatibility form into the canonical `-anlas-N` form.

## 3. What the estimate contains

The estimate response includes:

- base cost
- character reference cost
- Vibe encoding cost
- Vibe reference cost
- per-image cost
- requested samples
- billable samples
- strength factor
- Opus discount flag
- allowed / denied result
- reason / notes

## 4. Opus free tier

The SDK applies Opus discount when the request is within its lightweight limits.

Typically the discount is active when:

- `is_opus = true`
- total image area is not larger than 1,048,576 pixels
- steps are not greater than 28

The effect is usually:

- one sample becomes free
- `billable_samples = n_samples - 1`

This applies to normal text-to-image, image-to-image, and inpainting when the SDK decides it is allowed.

## 5. Img2Img / Inpainting strength factor

For image-to-image and inpainting, the SDK uses a `strength_factor`:

- `i2i.image` -> `strength`
- `inpaint.mask` -> `inpaintImg2ImgStrength`

Lower strength usually reduces cost.
The gateway displays this value in the estimate breakdown.

## 6. Character Reference cost

Character Reference costs a fixed extra amount per reference per generated sample.

Gateway display:
- `character_reference`

## 7. Vibe Transfer cost

Vibe has two parts:

### 7.1 Encoding cost
For uncached Vibe images, a fixed encoding cost applies.

### 7.2 Reference cost
If there are more than 4 Vibe references, the extra references cost more.

The gateway reports:
- `vibe_encoding`
- `vibe_reference`
- `cached_vibe_count`
- `uncached_vibe_count`

## 8. Why the frontend shows detailed cost labels

The frontend needs to know:

- whether the user is within the limit
- why the number is high
- whether the user is being charged because of CR or Vibe
- whether Opus discount is in effect

That is why the estimate response contains a full breakdown rather than a single integer.

## 9. Recommended frontend display

Suggested display order:

1. Total
2. Base
3. Character Reference
4. Vibe encoding
5. Vibe reference
6. Opus discount flag
7. Optional notes

## 10. Example

A 1024×1024, 28-step Opus request may estimate to:

- `per_image = 20`
- `billable_samples = 0`
- `total = 0`

For a single-sample Opus request, that is expected behavior under the SDK rules.

## 11. Practical warning

The SDK currently refuses widths or heights above 1600.
So the platform should clamp custom sizes to SDK-supported limits before submitting requests.

## 12. Summary

If the UI wants to be trustworthy, the best rule is:

- let the SDK decide the base cost
- present the breakdown transparently
- never silently override the SDK's result unless you are intentionally enforcing platform policy
