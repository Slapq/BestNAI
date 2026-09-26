"""High-level gateway client.

`YesNAI` / `AsyncYesNAI` provide the convenient surface, mirroring the NovelAI
SDK's shape:

```python
from yesnai import YesNAI
from yesnai.types import GenerateImageParams

with YesNAI(api_key="...", base_url="http://127.0.0.1:8010") as client:
    images = client.image.generate(GenerateImageParams(prompt="1girl"))
```

Low-level access is always available through `client.api_client`.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

from PIL import Image

from .._api.client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    GatewayResponse,
    _APIClient,
    _AsyncAPIClient,
)
from .._utils.converter import convert_request_to_native, convert_user_params_to_native
from .._utils.images import (
    decode_base64_image,
    extract_images_from_zip,
    extract_metadata_from_zip,
    iter_msgpack_frames,
    iter_sse_data,
)
from ..exceptions import InvalidRequestError, StreamError
from ..types.api.image import (
    EncodeVibeRequest,
    ImageGenerationRequest,
    ImageStreamChunk,
    StreamImageGenerationRequest,
)
from ..types.user.image import (
    AnnotateParams,
    AugmentParams,
    EncodeVibeParams,
    GenerateImageParams,
    GenerateImageStreamParams,
    UpscaleParams,
    image_to_base64,
)
from ..utils.anlas import AnlasEstimate, calculate_anlas


@dataclass(slots=True)
class GenerationResult:
    """A completed non-streaming generation."""

    images: list[Image.Image]
    gems_charged: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def save(self, path: str, index: int = 0, **kwargs: Any) -> None:
        """Save one generated image to disk."""
        self.images[index].save(path, **kwargs)


@dataclass(slots=True)
class JsonGenerationResult:
    """A `/v1/nai/generate-image` response."""

    images: list[Image.Image]
    image_format: str = "png"
    job: dict[str, Any] = field(default_factory=dict)

    def save(self, path: str, index: int = 0, **kwargs: Any) -> None:
        self.images[index].save(path, **kwargs)


@dataclass(slots=True)
class OpenAIImagesResult:
    """A `/v1/images/generations` response."""

    images: list[Image.Image]
    created: int = 0
    job: dict[str, Any] = field(default_factory=dict)

    def save(self, path: str, index: int = 0, **kwargs: Any) -> None:
        self.images[index].save(path, **kwargs)


@dataclass(slots=True)
class UploadResult:
    """An uploaded image reference."""

    upload_id: str
    sha256: str = ""
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    mime_type: str = "image/png"

    @property
    def reference(self) -> str:
        """The `upload_id:<id>` string to embed in a later payload."""
        return f"upload_id:{self.upload_id}"


def _resolve_api_key(api_key: str | None) -> str | None:
    return api_key or os.getenv("YESNAI_API_KEY") or os.getenv("BESTNAI_API_KEY")


class _ImageGenerationBase:
    """Shared helpers for the sync and async image surfaces."""

    @staticmethod
    def _decode_json_images(body: dict[str, Any]) -> JsonGenerationResult:
        encoded = body.get("images") or []
        images = [decode_base64_image(value) for value in encoded]
        return JsonGenerationResult(
            images=images,
            image_format=str(body.get("image_format") or "png"),
            job=body.get("job") or {},
        )

    @staticmethod
    def _decode_openai_images(body: dict[str, Any]) -> OpenAIImagesResult:
        images: list[Image.Image] = []
        for entry in body.get("data") or []:
            if "b64_json" in entry:
                images.append(decode_base64_image(entry["b64_json"]))
            elif "url" in entry and str(entry["url"]).startswith("data:"):
                images.append(decode_base64_image(entry["url"]))
        return OpenAIImagesResult(
            images=images,
            created=int(body.get("created") or 0),
            job=body.get("job") or {},
        )

    @staticmethod
    def _zip_result(response: GatewayResponse) -> GenerationResult:
        if response.content_type == "application/json":
            body = response.json()
            return GenerationResult(
                images=[decode_base64_image(value) for value in body.get("images") or []],
                gems_charged=response.gems_charged,
                metadata=body.get("metadata") or {},
            )
        return GenerationResult(
            images=extract_images_from_zip(response.content),
            gems_charged=response.gems_charged,
            metadata=extract_metadata_from_zip(response.content) or {},
        )

    @staticmethod
    def _chunk_from_frame(frame: dict[str, Any]) -> ImageStreamChunk:
        return ImageStreamChunk.from_frame(frame)

    @staticmethod
    def _encode_vibe_payload(params: EncodeVibeParams) -> dict[str, Any]:
        return {
            "image": image_to_base64(params.image),
            "information_extracted": params.information_extracted,
            "model": params.model,
        }


class ImageGeneration(_ImageGenerationBase):
    """High-level image generation (sync)."""

    def __init__(self, client: "YesNAI") -> None:
        self._client = client
        self._api = client.api_client

    # ── Native payload construction ──────────────────────────────────────────

    def to_native(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build the native payload for a request without sending it."""
        if isinstance(params, (ImageGenerationRequest, StreamImageGenerationRequest)):
            return convert_request_to_native(params)
        return convert_user_params_to_native(params, stream=stream)

    # ── Generation ───────────────────────────────────────────────────────────

    def generate(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        idempotency_key: str | None = None,
        native: bool = False,
    ) -> GenerationResult:
        """Generate images and return them as PIL images.

        `native=True` uses the strict `/native` surface, which never rewrites the
        payload; the default launcher surface returns the same ZIP shape.
        """
        payload = self.to_native(params)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = (
            self._api.image.generate_native(payload, headers=headers)
            if native
            else self._api.image.generate(payload, headers=headers)
        )
        return self._zip_result(response)

    def generate_json(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JsonGenerationResult:
        """Generate via `/v1/nai/generate-image` (JSON + base64)."""
        payload = self.to_native(params)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = self._api.request(
            "POST", "/v1/nai/generate-image", json_body=payload, headers=headers
        )
        return self._decode_json_images(response.json())

    def generate_openai(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        response_format: str = "b64_json",
        idempotency_key: str | None = None,
    ) -> OpenAIImagesResult:
        """Generate via `/v1/images/generations` ([OI] Images dialect)."""
        payload = self.to_native(params)
        parameters = payload.get("parameters") or {}
        width = int(parameters.get("width") or 512)
        height = int(parameters.get("height") or 512)
        body: dict[str, Any] = {
            "model": payload["model"],
            "prompt": payload["input"],
            "size": f"{width}x{height}",
            "n": int(parameters.get("n_samples") or 1),
            "response_format": response_format,
            "nai": {
                "steps": parameters.get("steps"),
                "sampler": parameters.get("sampler"),
                "scale": parameters.get("scale"),
                "seed": parameters.get("seed"),
                "negative_prompt": parameters.get("negative_prompt"),
                "noise_schedule": parameters.get("noise_schedule"),
                "qualityToggle": parameters.get("qualityToggle"),
                "ucPreset": parameters.get("uc_preset"),
            },
        }
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = self._api.request(
            "POST", "/v1/images/generations", json_body=body, headers=headers
        )
        return self._decode_openai_images(response.json())

    def stream(
        self,
        params: GenerateImageStreamParams | GenerateImageParams | StreamImageGenerationRequest,
        *,
        native: bool = False,
    ) -> Iterator[ImageStreamChunk]:
        """Stream generation frames.

        Frames are length-prefixed MessagePack. `native=True` relays the strict
        upstream stream from `/native`; otherwise the launcher stream is used.
        The two carry the same frame format.
        """
        payload = self.to_native(params, stream=True)
        chunks = (
            self._api.image.stream_native(payload) if native else self._api.image.stream(payload)
        )
        for frame in iter_msgpack_frames(chunks):
            yield self._chunk_from_frame(frame)

    # ── Pricing ──────────────────────────────────────────────────────────────

    def quote(self, params: GenerateImageParams | ImageGenerationRequest) -> dict[str, Any]:
        """Ask the gateway to price a request. Never charges."""
        return self._api.image.quote(self.to_native(params))

    def request_price(self, params: GenerateImageParams | ImageGenerationRequest) -> dict[str, Any]:
        """Ask the gateway for its local estimate."""
        return self._api.image.request_price(self.to_native(params))

    def estimate(
        self,
        params: GenerateImageParams,
        *,
        is_opus: bool = False,
        free_base_samples: int = 0,
    ) -> AnlasEstimate:
        """Estimate Anlas locally, without contacting the gateway.

        This is an estimate derived from a reverse-engineered formula; it is not
        an authoritative upstream charge.
        """
        width, height = params.resolved_size()
        features = _feature_counts(params)
        return calculate_anlas(
            model=params.model,
            width=width,
            height=height,
            steps=params.steps,
            n_samples=params.n_samples,
            is_opus=is_opus,
            free_base_samples=free_base_samples,
            **features,
        )

    # ── Tools ────────────────────────────────────────────────────────────────

    def encode_vibe(self, params: EncodeVibeParams) -> str:
        """Encode a vibe and return its base64 data."""
        response = self._api.image.encode_vibe(self._encode_vibe_payload(params))
        body = response.json()
        if isinstance(body, dict) and "data" in body:
            return str(body["data"])
        # The native surface returns raw bytes.
        return base64.b64encode(response.content).decode("ascii")

    def upscale(self, params: UpscaleParams) -> GenerationResult:
        response = self._api.image.upscale(
            {
                "image": image_to_base64(params.image),
                "scale": params.scale,
                "model": params.model,
            }
        )
        return self._zip_result(response)

    def augment(self, params: AugmentParams) -> GenerationResult:
        response = self._api.image.augment_image(
            {
                "image": image_to_base64(params.image),
                "req_type": params.req_type,
                "model": params.model,
            }
        )
        return self._zip_result(response)

    def annotate(self, params: AnnotateParams) -> GatewayResponse:
        return self._api.image.annotate_image(
            {
                "image": image_to_base64(params.image),
                "req_type": params.req_type,
                "model": params.model,
            }
        )

    def suggest_tags(self, prompt: str, *, model: str = "") -> list[str]:
        """Suggest tags for a partial prompt. Returns an empty list on failure."""
        body = self._api.image.suggest_tags(prompt=prompt, model=model)
        tags = body.get("tags") if isinstance(body, dict) else None
        return [str(tag) for tag in tags] if isinstance(tags, list) else []


class AsyncImageGeneration(_ImageGenerationBase):
    """High-level image generation (async)."""

    def __init__(self, client: "AsyncYesNAI") -> None:
        self._client = client
        self._api = client.api_client

    def to_native(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        stream: bool = False,
    ) -> dict[str, Any]:
        if isinstance(params, (ImageGenerationRequest, StreamImageGenerationRequest)):
            return convert_request_to_native(params)
        return convert_user_params_to_native(params, stream=stream)

    async def generate(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        idempotency_key: str | None = None,
        native: bool = False,
    ) -> GenerationResult:
        payload = self.to_native(params)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = (
            await self._api.image.generate_native(payload, headers=headers)
            if native
            else await self._api.image.generate(payload, headers=headers)
        )
        return self._zip_result(response)

    async def generate_json(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        idempotency_key: str | None = None,
    ) -> JsonGenerationResult:
        payload = self.to_native(params)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = await self._api.request(
            "POST", "/v1/nai/generate-image", json_body=payload, headers=headers
        )
        return self._decode_json_images(response.json())

    async def generate_openai(
        self,
        params: GenerateImageParams | ImageGenerationRequest,
        *,
        response_format: str = "b64_json",
        idempotency_key: str | None = None,
    ) -> OpenAIImagesResult:
        payload = self.to_native(params)
        parameters = payload.get("parameters") or {}
        width = int(parameters.get("width") or 512)
        height = int(parameters.get("height") or 512)
        body: dict[str, Any] = {
            "model": payload["model"],
            "prompt": payload["input"],
            "size": f"{width}x{height}",
            "n": int(parameters.get("n_samples") or 1),
            "response_format": response_format,
            "nai": {
                "steps": parameters.get("steps"),
                "sampler": parameters.get("sampler"),
                "scale": parameters.get("scale"),
                "seed": parameters.get("seed"),
                "negative_prompt": parameters.get("negative_prompt"),
                "noise_schedule": parameters.get("noise_schedule"),
                "qualityToggle": parameters.get("qualityToggle"),
                "ucPreset": parameters.get("uc_preset"),
            },
        }
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = await self._api.request(
            "POST", "/v1/images/generations", json_body=body, headers=headers
        )
        return self._decode_openai_images(response.json())

    async def stream(
        self,
        params: GenerateImageStreamParams | GenerateImageParams | StreamImageGenerationRequest,
        *,
        native: bool = False,
    ) -> AsyncIterator[ImageStreamChunk]:
        payload = self.to_native(params, stream=True)
        chunks = (
            self._api.image.stream_native(payload) if native else self._api.image.stream(payload)
        )
        async for frame in _aiter_msgpack_frames(chunks):
            yield self._chunk_from_frame(frame)

    async def quote(self, params: GenerateImageParams | ImageGenerationRequest) -> dict[str, Any]:
        return await self._api.image.quote(self.to_native(params))

    async def request_price(self, params: GenerateImageParams | ImageGenerationRequest) -> dict[str, Any]:
        return await self._api.image.request_price(self.to_native(params))

    def estimate(
        self,
        params: GenerateImageParams,
        *,
        is_opus: bool = False,
        free_base_samples: int = 0,
    ) -> AnlasEstimate:
        width, height = params.resolved_size()
        features = _feature_counts(params)
        return calculate_anlas(
            model=params.model,
            width=width,
            height=height,
            steps=params.steps,
            n_samples=params.n_samples,
            is_opus=is_opus,
            free_base_samples=free_base_samples,
            **features,
        )

    async def encode_vibe(self, params: EncodeVibeParams) -> str:
        response = await self._api.image.encode_vibe(self._encode_vibe_payload(params))
        body = response.json()
        if isinstance(body, dict) and "data" in body:
            return str(body["data"])
        return base64.b64encode(response.content).decode("ascii")

    async def upscale(self, params: UpscaleParams) -> GenerationResult:
        response = await self._api.image.upscale(
            {
                "image": image_to_base64(params.image),
                "scale": params.scale,
                "model": params.model,
            }
        )
        return self._zip_result(response)

    async def augment(self, params: AugmentParams) -> GenerationResult:
        response = await self._api.image.augment_image(
            {
                "image": image_to_base64(params.image),
                "req_type": params.req_type,
                "model": params.model,
            }
        )
        return self._zip_result(response)

    async def annotate(self, params: AnnotateParams) -> GatewayResponse:
        return await self._api.image.annotate_image(
            {
                "image": image_to_base64(params.image),
                "req_type": params.req_type,
                "model": params.model,
            }
        )

    async def suggest_tags(self, prompt: str, *, model: str = "") -> list[str]:
        body = await self._api.image.suggest_tags(prompt=prompt, model=model)
        tags = body.get("tags") if isinstance(body, dict) else None
        return [str(tag) for tag in tags] if isinstance(tags, list) else []


def _feature_counts(params: GenerateImageParams) -> dict[str, Any]:
    """Map high-level features onto the Anlas estimator's inputs."""
    character_references = params.character_references or []
    controlnet_images = params.controlnet.images if params.controlnet else []
    return {
        "character_reference_count": len(character_references),
        "uncached_vibe_count": len(controlnet_images),
        "vibe_reference_count": len(controlnet_images),
        "is_i2i": params.i2i is not None,
        "is_inpaint": params.inpaint is not None,
        "i2i_strength": params.i2i.strength if params.i2i else 1.0,
        "inpaint_strength": params.inpaint.strength if params.inpaint else 1.0,
    }


async def _aiter_msgpack_frames(chunks: AsyncIterator[bytes]) -> AsyncIterator[dict[str, Any]]:
    """Async equivalent of `iter_msgpack_frames`."""
    import struct

    buffer = bytearray()
    async for chunk in chunks:
        buffer.extend(chunk)
        while True:
            if len(buffer) < 4:
                break
            length = struct.unpack(">I", bytes(buffer[:4]))[0]
            if length > 192 * 1024 * 1024:
                raise StreamError(f"Stream frame length {length} exceeds the supported maximum")
            if len(buffer) < 4 + length:
                break
            payload = bytes(buffer[4 : 4 + length])
            del buffer[: 4 + length]
            import msgpack

            try:
                frame = msgpack.unpackb(payload, raw=False)
            except Exception as exc:
                raise StreamError("Stream frame was not valid MessagePack") from exc
            if isinstance(frame, dict):
                yield frame


class _Account:
    def __init__(self, client: Any) -> None:
        self._api = client.api_client

    def subscription(self) -> dict[str, Any]:
        return self._api.account.subscription()

    def native_subscription(self) -> dict[str, Any]:
        return self._api.account.native_subscription()

    def health(self) -> dict[str, Any]:
        return self._api.account.health()


class _AsyncAccount:
    def __init__(self, client: Any) -> None:
        self._api = client.api_client

    async def subscription(self) -> dict[str, Any]:
        return await self._api.account.subscription()

    async def native_subscription(self) -> dict[str, Any]:
        return await self._api.account.native_subscription()

    async def health(self) -> dict[str, Any]:
        return await self._api.account.health()


class _Models:
    def __init__(self, client: Any) -> None:
        self._api = client.api_client

    def list(self) -> list[str]:
        return self._api.models.list()

    def public_pricing(self) -> dict[str, Any]:
        return self._api.models.public_pricing()


class _AsyncModels:
    def __init__(self, client: Any) -> None:
        self._api = client.api_client

    async def list(self) -> list[str]:
        return await self._api.models.list()

    async def public_pricing(self) -> dict[str, Any]:
        return await self._api.models.public_pricing()


class _Upload:
    def __init__(self, client: Any) -> None:
        self._api = client.api_client

    def image(self, data: bytes, *, filename: str = "image.png", content_type: str = "image/png") -> UploadResult:
        return UploadResult(**self._api.upload.image(data, filename=filename, content_type=content_type))


class _AsyncUpload:
    def __init__(self, client: Any) -> None:
        self._api = client.api_client

    async def image(
        self, data: bytes, *, filename: str = "image.png", content_type: str = "image/png"
    ) -> UploadResult:
        body = await self._api.upload.image(data, filename=filename, content_type=content_type)
        return UploadResult(**body)


class YesNAI:
    """Synchronous BestNAI / YesNAI gateway client."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        headers: dict[str, str] | None = None,
        session_token: str | None = None,
    ) -> None:
        resolved = _resolve_api_key(api_key)
        if not resolved:
            raise InvalidRequestError(
                "An API key is required. Pass api_key=... or set YESNAI_API_KEY."
            )
        self.api_client = _APIClient(
            resolved,
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            session_token=session_token,
        )
        self.image = ImageGeneration(self)
        self.account = _Account(self)
        self.models = _Models(self)
        self.upload = _Upload(self)

    @property
    def api_key(self) -> str:
        return str(self.api_client.api_key)

    @property
    def base_url(self) -> str:
        return self.api_client.base_url

    def close(self) -> None:
        self.api_client.close()

    def __enter__(self) -> "YesNAI":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AsyncYesNAI:
    """Asynchronous BestNAI / YesNAI gateway client."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        headers: dict[str, str] | None = None,
        session_token: str | None = None,
    ) -> None:
        resolved = _resolve_api_key(api_key)
        if not resolved:
            raise InvalidRequestError(
                "An API key is required. Pass api_key=... or set YESNAI_API_KEY."
            )
        self.api_client = _AsyncAPIClient(
            resolved,
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            session_token=session_token,
        )
        self.image = AsyncImageGeneration(self)
        self.account = _AsyncAccount(self)
        self.models = _AsyncModels(self)
        self.upload = _AsyncUpload(self)

    @property
    def api_key(self) -> str:
        return str(self.api_client.api_key)

    @property
    def base_url(self) -> str:
        return self.api_client.base_url

    async def close(self) -> None:
        await self.api_client.close()

    async def __aenter__(self) -> "AsyncYesNAI":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
