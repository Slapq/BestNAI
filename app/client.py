"""SDK-backed NovelAI client.

This is the single place where BestNAI talks to NovelAI.  It accepts plain
native dictionaries at its boundary, but the upstream request body is always
built by the vendored NovelAI SDK (low-level Pydantic models or the high-level
converter).  No hand-rolled upstream payloads are produced here.

Ported from YesNovelAI `nai-adapter/app/client.py` with the platform-specific
provenance signing removed.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import math
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
from PIL import Image

from .key_policy import canonical_model_id, is_nai5_model, upstream_error_code
from .sdk_loader import ensure_sdk_on_path

ensure_sdk_on_path()

from novelai import AsyncNovelAI  # noqa: E402
from novelai._utils.converter import async_convert_user_params_to_api_request  # noqa: E402
from novelai.exceptions import (  # noqa: E402
    AuthenticationError,
    InvalidRequestError,
    MissingAPIKeyError,
    NetworkError,
    NovelAIError,
    RateLimitError,
    ServerError,
)
from novelai.types import GenerateImageParams, GenerateImageStreamParams  # noqa: E402
from novelai.types.api.image import EncodeVibeRequest, ImageGenerationRequest, StreamImageGenerationRequest  # noqa: E402


DEFAULT_IMAGE_BASE = "https://image.novelai.net"
DEFAULT_API_BASE = "https://api.novelai.net"

#: Marker used by the high-level compatibility path so a payload that happens to
#: carry `parameters` is still treated as high-level input.
REQUEST_FORMAT_KEY = "_yesnovelai_request_format"
REQUEST_FORMAT_HIGH_LEVEL = "bestnai_high_level"

_HIGH_LEVEL_TOP_LEVEL_KEYS = frozenset({
    "model",
    "action",
    "input",
    "prompt",
    "parameters",
    REQUEST_FORMAT_KEY,
})

#: High-level scalar parameters, mapped onto SDK user-param field names.
_HIGH_LEVEL_SCALAR_MAP: dict[str, str] = {
    "negative_prompt": "negative_prompt",
    "scale": "scale",
    "seed": "seed",
    "qualityToggle": "quality",
    "quality": "quality",
    "noise_schedule": "noise_schedule",
    "cfg_rescale": "cfg_rescale",
    "image_format": "image_format",
    "variety_boost": "variety_boost",
}

#: High-level feature parameters, passed through to the SDK user params.
_HIGH_LEVEL_FEATURE_KEYS: tuple[str, ...] = (
    "i2i",
    "inpaint",
    "controlnet",
    "character_references",
    "characters",
)

#: Undesired-content preset, accepted as either spelling.
_HIGH_LEVEL_UC_KEYS: tuple[str, ...] = ("uc_preset", "ucPreset")

#: Every `parameters` key the high-level converter understands.  Kept derived
#: from the converter's own maps so the detector can never drift out of sync.
_HIGH_LEVEL_PARAMETER_KEYS = frozenset(
    {
        "width",
        "height",
        "steps",
        "n_samples",
        "sampler",
        "stream",
    }
    | set(_HIGH_LEVEL_SCALAR_MAP)
    | set(_HIGH_LEVEL_FEATURE_KEYS)
    | set(_HIGH_LEVEL_UC_KEYS)
)

_UC_PRESET_NAMES: dict[object, str] = {
    0: "strong",
    1: "light",
    2: "furry_focus",
    3: "human_focus",
    4: "none",
}


class NovelAIAPIError(RuntimeError):
    """A normalized upstream error with a stable error code when available."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        body: str = "",
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.error_code = error_code


class NativeStreamResponse:
    """Owns an open raw NovelAI response while its pooled client stays reusable."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response

    async def close(self) -> None:
        await self.response.aclose()


@dataclass(frozen=True)
class StreamChunk:
    event_type: str
    samp_ix: int | None = None
    step_ix: int | None = None
    gen_id: int | None = None
    sigma: float | None = None
    image: str | None = None
    raw: dict[str, Any] | None = None

    @classmethod
    def from_sse_line(cls, line: str) -> "StreamChunk":
        if line.startswith("data: "):
            line = line[6:]
        data = json.loads(line)
        return cls(
            event_type=data.get("event_type") or data.get("event") or "unknown",
            samp_ix=data.get("samp_ix"),
            step_ix=data.get("step_ix"),
            gen_id=data.get("gen_id"),
            sigma=data.get("sigma"),
            image=data.get("image") or data.get("data"),
            raw=data,
        )

    @classmethod
    def from_sdk(cls, chunk: Any) -> "StreamChunk":
        raw = chunk.model_dump(mode="json") if hasattr(chunk, "model_dump") else dict(chunk)
        return cls(
            event_type=str(raw.get("event_type") or raw.get("event") or "unknown"),
            samp_ix=raw.get("samp_ix"),
            step_ix=raw.get("step_ix"),
            gen_id=raw.get("gen_id"),
            sigma=raw.get("sigma"),
            image=raw.get("image") or raw.get("data"),
            raw=raw,
        )


def image_to_base64(img: Image.Image, fmt: str = "png") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt.upper())
    return base64.b64encode(buf.getvalue()).decode("utf-8")


#: The vendored SDK raises `AuthenticationError` for *both* 401 and 402, using this
#: fixed message for the credits case (`novelai/_api/client.py::handle_response`).
#: Recognizing it restores the distinction: a pool balance problem must not be
#: reported as a credential failure.
_SDK_INSUFFICIENT_CREDITS_MESSAGE = "Insufficient credits or subscription required"


def _map_sdk_error(exc: Exception) -> NovelAIAPIError:
    if isinstance(exc, AuthenticationError):
        text = str(exc)
        if _SDK_INSUFFICIENT_CREDITS_MESSAGE in text:
            return NovelAIAPIError(
                402,
                text,
                error_code=upstream_error_code(402),
            )
        raw_status = getattr(exc, "status_code", None)
        status = int(raw_status) if isinstance(raw_status, int) else 401
        error_code = getattr(exc, "error_code", None) or upstream_error_code(status)
        return NovelAIAPIError(status, text, error_code=error_code)
    if isinstance(exc, MissingAPIKeyError):
        return NovelAIAPIError(401, str(exc), error_code=upstream_error_code(401))
    if isinstance(exc, InvalidRequestError):
        return NovelAIAPIError(400, str(exc))
    if isinstance(exc, RateLimitError):
        return NovelAIAPIError(429, str(exc))
    if isinstance(exc, (ServerError, NetworkError, NovelAIError)):
        return NovelAIAPIError(502, str(exc))
    if isinstance(exc, httpx.HTTPError):
        return NovelAIAPIError(502, str(exc))
    return NovelAIAPIError(500, str(exc))


def _parse_official_price(content: bytes) -> tuple[float, object]:
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise NovelAIAPIError(502, "NovelAI price response was not valid JSON") from exc
    value: object
    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        value = parsed
    elif isinstance(parsed, dict):
        value = parsed.get("price")
    else:
        value = None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise NovelAIAPIError(502, "NovelAI price response did not contain a valid price")
    return float(value), parsed


def _decode_inline_image(value: Any) -> Any:
    """Turn an inline base64 image string into raw bytes.

    The vendored SDK's high-level converter decides between "already base64" and
    "file path" by looking for path separators.  Real base64 PNG/WebP data
    routinely contains `/`, so that heuristic misreads inline data as a path.
    Decoding to bytes here removes the ambiguity: the converter re-encodes bytes
    without guessing.

    Values that are not inline image data (an `upload_id:` reference, a dict, a
    PIL image) are returned unchanged.
    """
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate:
        return value
    if candidate.startswith("upload_id:"):
        return value
    if candidate.startswith("data:"):
        _, _, candidate = candidate.partition(",")
    # Only treat the string as base64 when it actually decodes as base64.
    try:
        return base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError):
        return value


def _decode_inline_images_in_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Normalize every inline image inside a high-level `parameters` object."""
    result = dict(parameters)

    for key in ("i2i", "inpaint"):
        block = result.get(key)
        if isinstance(block, dict):
            result[key] = {
                name: _decode_inline_image(field) for name, field in block.items()
            }

    controlnet = result.get("controlnet")
    if isinstance(controlnet, dict):
        images = controlnet.get("images")
        if isinstance(images, list):
            controlnet = dict(controlnet)
            controlnet["images"] = [
                {**entry, "image": _decode_inline_image(entry.get("image"))}
                if isinstance(entry, dict)
                else entry
                for entry in images
            ]
            result["controlnet"] = controlnet

    # `director_reference_images` is a list of base64 strings (low-level).
    entries = result.get("director_reference_images")
    if isinstance(entries, list):
        result["director_reference_images"] = [_decode_inline_image(entry) for entry in entries]

    # `character_references` is a list of objects carrying the image plus its type.
    references = result.get("character_references")
    if isinstance(references, list):
        result["character_references"] = [
            {**entry, "image": _decode_inline_image(entry.get("image"))}
            if isinstance(entry, dict)
            else entry
            for entry in references
        ]

    return result


def native_to_user_params(payload: dict[str, Any], *, stream: bool = False) -> dict[str, Any]:
    """Normalize a YesNAI native payload into SDK high-level user params.

    This may only prepare fields accepted by `GenerateImageParams`.  It must not
    construct NovelAI low-level request JSON.
    """
    params = payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {}
    params = _decode_inline_images_in_parameters(params)
    width = int(params.get("width") or 512)
    height = int(params.get("height") or 512)
    data: dict[str, Any] = {
        "model": canonical_model_id(payload.get("model")),
        "prompt": str(payload.get("input") or payload.get("prompt") or ""),
        "size": (width, height),
        "steps": int(params.get("steps") or 28),
        "n_samples": int(params.get("n_samples") or 1),
        "sampler": str(params.get("sampler") or "k_euler_ancestral"),
    }
    for source, target in _HIGH_LEVEL_SCALAR_MAP.items():
        if source in params and params[source] is not None:
            data[target] = params[source]
    for key in _HIGH_LEVEL_FEATURE_KEYS:
        if key in params and params[key] is not None:
            data[key] = params[key]
    uc_value = params.get("uc_preset", params.get("ucPreset"))
    if uc_value is not None:
        data["uc_preset"] = _UC_PRESET_NAMES.get(uc_value, uc_value)
    if stream:
        data["stream"] = True
    return data


def looks_like_low_level(payload: dict[str, Any], *, stream: bool = False) -> bool:
    """Decide whether a native payload is a low-level NovelAI request.

    Low-level payloads are validated and forwarded as-is; high-level payloads go
    through the SDK converter.
    """
    if payload.get(REQUEST_FORMAT_KEY) == REQUEST_FORMAT_HIGH_LEVEL:
        return False
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return False
    action = str(payload.get("action") or "").strip().lower()
    model = canonical_model_id(payload.get("model"))
    if action in {"img2img", "infill", "inpaint"}:
        return True
    if model.endswith("-inpainting"):
        return True
    if payload.get("use_new_shared_trial") is not None:
        return True
    if set(payload) - _HIGH_LEVEL_TOP_LEVEL_KEYS:
        return True
    if set(parameters) - _HIGH_LEVEL_PARAMETER_KEYS:
        return True
    if stream and parameters.get("stream") is not None:
        return True
    return False


def build_low_level_request(
    payload: dict[str, Any],
    *,
    stream: bool = False,
) -> ImageGenerationRequest | StreamImageGenerationRequest:
    """Validate a low-level payload with the SDK models, preserving caller fields."""
    data = dict(payload)
    data["model"] = canonical_model_id(data.get("model"))
    data.setdefault("use_new_shared_trial", True)
    params = dict(data.get("parameters") or {})
    width = int(params.get("width") or 0)
    height = int(params.get("height") or 0)
    if width and height:
        if width > 2048 or height > 2048 or width * height > 3_145_728:
            raise ValueError("Image resolution exceeds NovelAI's supported 3,145,728-pixel limit")
        if width < 64 or height < 64 or width % 64 != 0 or height % 64 != 0:
            raise ValueError("Image dimensions must be multiples of 64 between 64 and 2048")
    model = str(data.get("model") or "")
    if params.get("sm") and model not in {
        "nai-diffusion-3",
        "nai-diffusion-3-inpainting",
        "nai-diffusion-3-furry",
        "nai-diffusion-3-furry-inpainting",
    }:
        raise ValueError("SMEA and SMEA Dyn are only supported by active V3 models")
    data["parameters"] = params
    if stream:
        params.setdefault("stream", "sse")
        return StreamImageGenerationRequest.model_validate(data)
    return ImageGenerationRequest.model_validate(data)


class NovelAIClient:
    """SDK-backed NovelAI image client bound to one upstream key.

    Instances are long-lived and reused across requests by the scheduler; the
    HTTP clients are only closed when the key leaves the pool.
    """

    def __init__(
        self,
        api_key: str,
        *,
        image_base: str = DEFAULT_IMAGE_BASE,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.image_base = image_base.rstrip("/")
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._client = AsyncNovelAI(
            api_key=api_key,
            image_base=image_base,
            api_base=api_base,
            timeout=timeout,
        )
        self._raw_client = httpx.AsyncClient(
            timeout=300.0,
            headers={"Authorization": f"Bearer {self.api_key}"},
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=90.0,
            ),
        )

    async def close(self) -> None:
        await self._client.close()
        await self._raw_client.aclose()

    async def __aenter__(self) -> "NovelAIClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ── Request preparation ──────────────────────────────────────────────────

    async def prepare_image_request(
        self,
        payload: dict[str, Any],
        *,
        stream: bool = False,
    ) -> ImageGenerationRequest | StreamImageGenerationRequest:
        if looks_like_low_level(payload, stream=stream):
            return build_low_level_request(payload, stream=stream)
        params_type = GenerateImageStreamParams if stream else GenerateImageParams
        params = params_type.model_validate(native_to_user_params(payload, stream=stream))
        return await async_convert_user_params_to_api_request(params, self._client)

    # ── Official pricing ─────────────────────────────────────────────────────

    async def official_tier(self, tier: str | None) -> str:
        if tier in {"TABLET", "SCROLL", "OPUS"}:
            return tier
        snapshot = await self.get_account_snapshot()
        if snapshot.get("subscription_active") is not True:
            raise NovelAIAPIError(402, "An active NovelAI subscription is required")
        resolved = {1: "TABLET", 2: "SCROLL", 3: "OPUS"}.get(
            int(snapshot.get("subscription_tier") or 0)
        )
        if resolved is None:
            raise NovelAIAPIError(402, "NovelAI subscription tier cannot price image generation")
        return resolved

    async def request_official_image_price(
        self,
        payload: dict[str, Any],
        *,
        tier: str | None = None,
    ) -> dict[str, Any]:
        official_tier = await self.official_tier(tier)
        content, _ = await self.raw_request(
            path="/ai/generate-image/request-price",
            payload={"tier": official_tier, "request": payload},
            api_host="main",
            timeout=45.0,
        )
        price, upstream = _parse_official_price(content)
        return {"price": price, "tier": official_tier, "upstream": upstream}

    async def request_image_price(
        self,
        payload: dict[str, Any],
        *,
        tier: str | None = None,
    ) -> dict[str, Any]:
        if not is_nai5_model(payload.get("model")):
            raise NovelAIAPIError(400, "Official image pricing is reserved for NAI Diffusion V5")
        try:
            request = await self.prepare_image_request(payload)
            body = request.model_dump(mode="json", exclude_none=True)
            return await self.request_official_image_price(body, tier=tier)
        except NovelAIAPIError:
            raise
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

    # ── Generation ───────────────────────────────────────────────────────────

    async def generate_image(self, payload: dict[str, Any]) -> list[Image.Image]:
        """Generate images via the SDK low-level API."""
        try:
            request = await self.prepare_image_request(payload)
            return await self._client.api_client.image.generate(request)  # type: ignore[arg-type]
        except NovelAIAPIError:
            raise
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

    async def generate_image_stream(self, payload: dict[str, Any]) -> AsyncIterator[StreamChunk]:
        """Generate images via the SDK streaming API."""
        try:
            request = await self.prepare_image_request(payload, stream=True)
            async for chunk in self._client.api_client.image.generate_stream(request):  # type: ignore[arg-type]
                yield StreamChunk.from_sdk(chunk)
        except NovelAIAPIError:
            raise
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

    async def encode_vibe(
        self,
        *,
        image_b64: str,
        information_extracted: float,
        model: str,
    ) -> str:
        try:
            request = EncodeVibeRequest(
                image=image_b64,
                information_extracted=information_extracted,
                model=model,
            )
            return await self._client.api_client.image.encode_vibe(request)
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

    # ── Raw upstream access ──────────────────────────────────────────────────

    async def open_raw_generate_stream(self, payload: dict[str, Any]) -> NativeStreamResponse:
        """Open the upstream MessagePack stream without SDK decoding."""
        request = self._raw_client.build_request(
            "POST",
            f"{self.image_base}/ai/generate-image-stream",
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/x-msgpack",
            },
        )
        try:
            response = await self._raw_client.send(request, stream=True)
            if response.status_code >= 400:
                body = (await response.aread()).decode(errors="replace")
                await response.aclose()
                raise NovelAIAPIError(
                    response.status_code,
                    f"NovelAI request failed with HTTP {response.status_code}",
                    body=body,
                    error_code=upstream_error_code(response.status_code),
                )
            return NativeStreamResponse(response)
        except NovelAIAPIError:
            raise
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

    async def get_account_snapshot(self) -> dict[str, Any]:
        """Read the upstream account subscription and information state."""
        subscription_bytes, _ = await self.raw_get(
            path="/user/subscription",
            params={},
            api_host="image",
        )
        try:
            subscription = json.loads(subscription_bytes)
        except (TypeError, json.JSONDecodeError) as exc:
            raise NovelAIAPIError(502, "NovelAI subscription response was not valid JSON") from exc
        if not isinstance(subscription, dict):
            raise NovelAIAPIError(502, "NovelAI subscription response was not an object")

        information: dict[str, Any] = {}
        information_error = ""
        try:
            information_bytes, _ = await self.raw_get(
                path="/user/information",
                params={},
                api_host="image",
            )
            parsed_information = json.loads(information_bytes)
            if isinstance(parsed_information, dict):
                information = parsed_information
            else:
                information_error = "NovelAI account information response was not an object"
        except NovelAIAPIError as exc:
            information_error = f"NovelAI account information returned HTTP {exc.status_code}"
        except (TypeError, json.JSONDecodeError):
            information_error = "NovelAI account information response was not valid JSON"

        steps = subscription.get("trainingStepsLeft")
        if not isinstance(steps, dict):
            steps = {}
        usage = subscription.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        usage_percent_value = usage.get("percent")
        try:
            nai5_usage_percent = (
                float(usage_percent_value)
                if usage_percent_value is not None and not isinstance(usage_percent_value, bool)
                else None
            )
        except (TypeError, ValueError):
            nai5_usage_percent = None
        nai5_is_negative = usage.get("isNegative") if isinstance(usage.get("isNegative"), bool) else None
        fixed_anlas = max(0, int(steps.get("fixedTrainingStepsLeft") or 0))
        purchased_anlas = max(0, int(steps.get("purchasedTrainingSteps") or 0))
        tier = int(subscription.get("tier") or 0)
        ban_status = str(information.get("banStatus") or "")[:64]
        subscription_active = bool(subscription.get("active"))
        banned_statuses = {"banned", "suspended", "blocked", "terminated"}
        account_status = "banned" if ban_status.lower() in banned_statuses else (
            "active" if subscription_active else "inactive"
        )
        return {
            "subscription_tier": tier,
            "subscription_active": subscription_active,
            "subscription_expires_at": subscription.get("expiresAt"),
            "fixed_anlas": fixed_anlas,
            "purchased_anlas": purchased_anlas,
            "total_anlas": fixed_anlas + purchased_anlas,
            "nai5_usage_percent": nai5_usage_percent,
            "nai5_is_negative": nai5_is_negative,
            "is_opus": tier == 3 and subscription_active,
            "is_grace_period": bool(subscription.get("isGracePeriod")),
            "email_verified": information.get("emailVerified"),
            "trial_activated": information.get("trialActivated"),
            "trial_images_left": information.get("trialImagesLeft"),
            "ban_status": ban_status,
            "account_status": account_status,
            "information_error": information_error,
        }

    async def raw_get(
        self,
        *,
        path: str,
        params: dict[str, str],
        api_host: str = "image",
    ) -> tuple[bytes, str]:
        base_url = self.api_base if api_host == "main" else self.image_base
        try:
            response = await self._raw_client.get(
                f"{base_url}/{path.lstrip('/')}",
                params=params,
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                raise NovelAIAPIError(
                    response.status_code,
                    f"NovelAI request failed with HTTP {response.status_code}",
                    body=response.text,
                    error_code=upstream_error_code(response.status_code),
                )
            return response.content, response.headers.get("content-type", "application/json")
        except NovelAIAPIError:
            raise
        except Exception as exc:
            raise _map_sdk_error(exc) from exc

    async def raw_request(
        self,
        *,
        path: str,
        payload: dict[str, Any],
        api_host: str = "image",
        accept: str | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, str]:
        base_url = self.api_base if api_host == "main" else self.image_base
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if accept:
            headers["Accept"] = accept
        try:
            response = await self._raw_client.post(
                f"{base_url}/{path.lstrip('/')}",
                json=payload,
                headers=headers,
                timeout=timeout or self.timeout,
            )
            if response.status_code >= 400:
                raise NovelAIAPIError(
                    response.status_code,
                    f"NovelAI request failed with HTTP {response.status_code}",
                    body=response.text,
                    error_code=upstream_error_code(response.status_code),
                )
            return response.content, response.headers.get("content-type", "application/octet-stream")
        except NovelAIAPIError:
            raise
        except Exception as exc:
            raise _map_sdk_error(exc) from exc
