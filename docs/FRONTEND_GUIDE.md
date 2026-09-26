# BestNAI Frontend Guide

This guide explains the single-page frontend in `frontend/index.html`.

## 1. What the frontend is responsible for

The frontend is not a simple form.
It is the user-facing control surface for:

- prompt editing
- parameter tuning
- upload management
- live cost estimation
- generation and download
- BYOK mode
- i2i / inpaint mask editing
- CR and Vibe management
- multi-character layout

## 2. Main UI sections

### Prompt section
- prompt
- negative prompt
- model selector
- anlas limit selector
- size selector
- steps
- scale
- sampler
- image format

### Advanced settings
- quality tags
- variety boost
- CFG rescale
- custom width / height

### I2I / Inpaint tab
- i2i source upload
- inpaint source upload
- canvas-based mask editor
- brush size
- brush / eraser / clear / invert
- strength slider
- noise slider (i2i)

### Reference tab
- Character Reference upload
- Vibe upload
- multiple entries with individual sliders

### Characters tab
- multi-character prompt entries
- 5×5 position grid

### Actions
- Generate
- Stream
- ZIP
- BYOK toggle

## 3. Live estimation

The frontend continuously re-estimates Anlas when the user changes:

- prompt fields
- model
- size
- steps
- scale
- i2i strength
- inpaint strength
- brush size
- CR / Vibe / character entries

This gives immediate feedback before generation.

## 4. BYOK behavior

When BYOK is enabled:

- the frontend adds `X-NAI-Key`
- generation uses the BYOK endpoints
- estimation uses `/v1/estimate/byok`

## 5. Streaming behavior

The frontend stream button uses the stream endpoints and defaults to fake-stream mode.

This is intended to improve perceived speed:

- the user sees the stream container immediately
- the first visible image appears sooner
- the UI feels responsive even if the server is still doing its work internally

## 6. i2i / inpaint mask editor

The inpaint editor is canvas-based.

### Mask convention
- white = repaint
- black = keep

### Interaction model
- upload source image
- paint over the target region
- optionally erase or invert
- the frontend exports a PNG mask automatically

### Why this design
It is much closer to official inpainting workflows than asking the user to upload a separate mask file manually.

## 7. Suggested UX improvements

If you want to keep improving the frontend, prioritize:

- loading spinners on all actions
- error details in the UI
- request history panel
- image metadata panel
- better mobile responsiveness
- drag handles for the character layout grid

## 8. Practical frontend rules

- always call estimate before generate
- always keep upload IDs if possible
- prefer webp preview, png download
- do not store admin credentials in the browser
- treat stream errors as first-class UI events
