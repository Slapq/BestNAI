"""Low-level HTTP client for the BestNAI / YesNAI gateway.

This layer knows about endpoints and error envelopes; it does no parameter
conversion.  `_client/client.py` builds the convenient surface on top of it.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

import httpx

from ..exceptions import NetworkError, YesNAIError, exception_for

DEFAULT_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_TIMEOUT = 300.0

#: Header carrying the Gems price of a completed generation.
GEMS_CHARGED_HEADER = "X-YesNAI-Gems-Charged"


def parse_error_response(response: httpx.Response) -> YesNAIError:
    """Build an exception from any of the gateway's error envelopes.

    The gateway answers in four dialects, so all four are recognised here:

    * `{"error": {"code", "message", "status_code"}}` — admin / legacy
    * `{"error": {"message", "type", "code"}}`       — [OI]-compatible
    * `{"message", "code", "request_id"}`            — NovelAI native
    * `{"message", "code"}`                          — Aaalice Launcher
    * `{"detail": ...}`                              — FastAPI default
    """
    status = response.status_code
    request_id = response.headers.get("x-request-id")
    try:
        body: Any = response.json()
    except (ValueError, json.JSONDecodeError):
        text = response.text.strip()
        return exception_for(status, None, text or f"HTTP {status}", request_id=request_id)

    code: str | None = None
    message: str | None = None
    details: Any = None

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code") or None
            message = error.get("message") or None
            details = error.get("details")
        elif isinstance(error, str):
            message = error
        if message is None:
            message = body.get("message") or None
            code = code or body.get("code") or None
            request_id = body.get("request_id") or request_id
            if details is None:
                details = body.get("details")
        if message is None and "detail" in body:
            detail = body["detail"]
            if isinstance(detail, dict):
                message = detail.get("message")
                code = code or detail.get("code")
                details = details if details is not None else detail
            else:
                message = str(detail)

    if message is None:
        message = response.text.strip() or f"HTTP {status}"
    return exception_for(status, code, str(message), request_id=request_id, details=details)


class GatewayResponse:
    """A raw gateway response plus its decoded metadata."""

    def __init__(self, response: httpx.Response, content: bytes) -> None:
        self.status_code = response.status_code
        self.headers = response.headers
        self.content = content

    @property
    def gems_charged(self) -> int | None:
        """The Gems price the gateway reported, when present."""
        raw = self.headers.get(GEMS_CHARGED_HEADER)
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip()

    def json(self) -> Any:
        return json.loads(self.content)


class _BaseGatewayClient:
    """Shared request plumbing."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        headers: dict[str, str] | None = None,
        session_token: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_token = session_token
        self._extra_headers = dict(headers or {})

    @property
    def has_session(self) -> bool:
        """Whether a login session is available for user-management endpoints.

        YesNAI protects its estimate endpoint with the login JWT rather than the
        `ynai-...` API token, so both credential kinds may be needed.
        """
        return bool(self.session_token)

    def _headers(
        self,
        extra: dict[str, str] | None = None,
        *,
        session: bool = False,
    ) -> dict[str, str]:
        headers = {"Accept": "application/json", **self._extra_headers}
        credential = self.session_token if session else self.api_key
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"


class _APIClient(_BaseGatewayClient):
    """Synchronous low-level gateway client."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http = httpx.Client(timeout=self.timeout, follow_redirects=True)
        self.image = ImageAPI(self)
        self.account = AccountAPI(self)
        self.models = ModelAPI(self)
        self.upload = UploadAPI(self)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "_APIClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: Any = None,
        data: Any = None,
        session: bool = False,
    ) -> GatewayResponse:
        try:
            response = self._http.request(
                method,
                self._url(path),
                json=json_body,
                content=content,
                params=params,
                headers=self._headers(headers, session=session),
                files=files,
                data=data,
            )
        except httpx.RequestError as exc:
            raise NetworkError(f"Could not reach the gateway: {exc}") from exc
        if response.status_code >= 400:
            raise parse_error_response(response)
        return GatewayResponse(response, response.content)

    def stream(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[bytes]:
        try:
            with self._http.stream(
                method,
                self._url(path),
                json=json_body,
                params=params,
                headers=self._headers(headers),
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise parse_error_response(response)
                yield from response.iter_bytes()
        except httpx.RequestError as exc:
            raise NetworkError(f"Could not reach the gateway: {exc}") from exc


class _AsyncAPIClient(_BaseGatewayClient):
    """Asynchronous low-level gateway client."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        self.image = AsyncImageAPI(self)
        self.account = AsyncAccountAPI(self)
        self.models = AsyncModelAPI(self)
        self.upload = AsyncUploadAPI(self)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "_AsyncAPIClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: Any = None,
        data: Any = None,
        session: bool = False,
    ) -> GatewayResponse:
        try:
            response = await self._http.request(
                method,
                self._url(path),
                json=json_body,
                content=content,
                params=params,
                headers=self._headers(headers, session=session),
                files=files,
                data=data,
            )
        except httpx.RequestError as exc:
            raise NetworkError(f"Could not reach the gateway: {exc}") from exc
        if response.status_code >= 400:
            raise parse_error_response(response)
        return GatewayResponse(response, response.content)

    async def stream(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[bytes]:
        try:
            async with self._http.stream(
                method,
                self._url(path),
                json=json_body,
                params=params,
                headers=self._headers(headers),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise parse_error_response(response)
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.RequestError as exc:
            raise NetworkError(f"Could not reach the gateway: {exc}") from exc


class ImageAPI:
    """Low-level image endpoints (sync)."""

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    def generate(
        self,
        payload: dict[str, Any],
        *,
        response_format: str = "zip",
        headers: dict[str, str] | None = None,
    ) -> GatewayResponse:
        """POST `/ai/generate-image` (ZIP by default)."""
        return self._client.request(
            "POST",
            "/ai/generate-image",
            json_body=payload,
            params={"response_format": response_format} if response_format else None,
            headers={"Accept": "application/zip", **(headers or {})},
        )

    def generate_native(
        self, payload: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> GatewayResponse:
        """POST `/native/ai/generate-image` (ZIP)."""
        return self._client.request(
            "POST",
            "/native/ai/generate-image",
            json_body=payload,
            headers={"Accept": "application/x-zip-compressed", **(headers or {})},
        )

    def generate_json(
        self, payload: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> GatewayResponse:
        """POST `/v1/nai/generate-image` (JSON + base64)."""
        return self._client.request(
            "POST", "/v1/nai/generate-image", json_body=payload, headers=headers
        )

    def generate_openai(
        self, payload: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> GatewayResponse:
        """POST `/v1/images/generations` ([OI] Images)."""
        return self._client.request(
            "POST", "/v1/images/generations", json_body=payload, headers=headers
        )

    def chat_completions(self, payload: dict[str, Any]) -> GatewayResponse:
        """POST `/v1/chat/completions` ([OI] Chat)."""
        return self._client.request("POST", "/v1/chat/completions", json_body=payload)

    def quote(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `/ai/generate-image/quote` (never charges)."""
        return self._client.request("POST", "/ai/generate-image/quote", json_body=payload).json()

    def request_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Estimate a request.

        Uses YesNAI's `POST /api/ynai/images/request-price` when a session token is
        configured, because that endpoint requires the login JWT. Otherwise uses
        BestNAI's `POST /v1/nai/request-price`, which accepts the API token.
        """
        if self._client.has_session:
            response = self._client.request(
                "POST", "/api/ynai/images/request-price", json_body=payload, session=True
            )
        else:
            response = self._client.request("POST", "/v1/nai/request-price", json_body=payload)
        return response.json()

    def stream(self, payload: dict[str, Any]) -> Iterator[bytes]:
        """POST `/ai/generate-image-stream` (length-prefixed MessagePack)."""
        return self._client.stream(
            "POST",
            "/ai/generate-image-stream",
            json_body=payload,
            headers={"Accept": "application/x-msgpack"},
        )

    def stream_native(self, payload: dict[str, Any]) -> Iterator[bytes]:
        """POST `/native/ai/generate-image-stream` (verbatim upstream frames)."""
        return self._client.stream(
            "POST",
            "/native/ai/generate-image-stream",
            json_body=payload,
            headers={"Accept": "application/x-msgpack"},
        )

    def encode_vibe(self, payload: dict[str, Any]) -> GatewayResponse:
        """POST `/ai/encode-vibe`."""
        return self._client.request("POST", "/ai/encode-vibe", json_body=payload)

    def upscale(self, payload: dict[str, Any]) -> GatewayResponse:
        """POST `/ai/upscale`."""
        return self._client.request("POST", "/ai/upscale", json_body=payload)

    def augment_image(self, payload: dict[str, Any]) -> GatewayResponse:
        """POST `/ai/augment-image`."""
        return self._client.request("POST", "/ai/augment-image", json_body=payload)

    def annotate_image(self, payload: dict[str, Any]) -> GatewayResponse:
        """POST `/ai/annotate-image`."""
        return self._client.request("POST", "/ai/annotate-image", json_body=payload)

    def suggest_tags(self, *, prompt: str, model: str = "") -> dict[str, Any]:
        """GET `/ai/generate-image/suggest-tags`."""
        params = {"prompt": prompt}
        if model:
            params["model"] = model
        return self._client.request("GET", "/ai/generate-image/suggest-tags", params=params).json()


class AsyncImageAPI:
    """Low-level image endpoints (async)."""

    def __init__(self, client: _AsyncAPIClient) -> None:
        self._client = client

    async def generate(
        self,
        payload: dict[str, Any],
        *,
        response_format: str = "zip",
        headers: dict[str, str] | None = None,
    ) -> GatewayResponse:
        return await self._client.request(
            "POST",
            "/ai/generate-image",
            json_body=payload,
            params={"response_format": response_format} if response_format else None,
            headers={"Accept": "application/zip", **(headers or {})},
        )

    async def generate_native(
        self, payload: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> GatewayResponse:
        return await self._client.request(
            "POST",
            "/native/ai/generate-image",
            json_body=payload,
            headers={"Accept": "application/x-zip-compressed", **(headers or {})},
        )

    async def generate_json(
        self, payload: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> GatewayResponse:
        return await self._client.request(
            "POST", "/v1/nai/generate-image", json_body=payload, headers=headers
        )

    async def generate_openai(
        self, payload: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> GatewayResponse:
        return await self._client.request(
            "POST", "/v1/images/generations", json_body=payload, headers=headers
        )

    async def chat_completions(self, payload: dict[str, Any]) -> GatewayResponse:
        return await self._client.request("POST", "/v1/chat/completions", json_body=payload)

    async def quote(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.request("POST", "/ai/generate-image/quote", json_body=payload)
        return response.json()

    async def request_price(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Estimate a request. See the sync variant for the dialect selection."""
        if self._client.has_session:
            response = await self._client.request(
                "POST", "/api/ynai/images/request-price", json_body=payload, session=True
            )
        else:
            response = await self._client.request(
                "POST", "/v1/nai/request-price", json_body=payload
            )
        return response.json()

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        return self._client.stream(
            "POST",
            "/ai/generate-image-stream",
            json_body=payload,
            headers={"Accept": "application/x-msgpack"},
        )

    def stream_native(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        return self._client.stream(
            "POST",
            "/native/ai/generate-image-stream",
            json_body=payload,
            headers={"Accept": "application/x-msgpack"},
        )

    async def encode_vibe(self, payload: dict[str, Any]) -> GatewayResponse:
        return await self._client.request("POST", "/ai/encode-vibe", json_body=payload)

    async def upscale(self, payload: dict[str, Any]) -> GatewayResponse:
        return await self._client.request("POST", "/ai/upscale", json_body=payload)

    async def augment_image(self, payload: dict[str, Any]) -> GatewayResponse:
        return await self._client.request("POST", "/ai/augment-image", json_body=payload)

    async def annotate_image(self, payload: dict[str, Any]) -> GatewayResponse:
        return await self._client.request("POST", "/ai/annotate-image", json_body=payload)

    async def suggest_tags(self, *, prompt: str, model: str = "") -> dict[str, Any]:
        params = {"prompt": prompt}
        if model:
            params["model"] = model
        response = await self._client.request("GET", "/ai/generate-image/suggest-tags", params=params)
        return response.json()


class AccountAPI:
    """Account and catalogue endpoints (sync)."""

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    def subscription(self) -> dict[str, Any]:
        """GET `/user/subscription` (Launcher dialect, includes `currency`)."""
        return self._client.request("GET", "/user/subscription").json()

    def native_subscription(self) -> dict[str, Any]:
        """GET `/native/user/subscription` (NovelAI native dialect)."""
        return self._client.request("GET", "/native/user/subscription").json()

    def health(self) -> dict[str, Any]:
        """GET `/health` — includes pool and queue counters."""
        return self._client.request("GET", "/health").json()


class AsyncAccountAPI:
    """Account and catalogue endpoints (async)."""

    def __init__(self, client: _AsyncAPIClient) -> None:
        self._client = client

    async def subscription(self) -> dict[str, Any]:
        return (await self._client.request("GET", "/user/subscription")).json()

    async def native_subscription(self) -> dict[str, Any]:
        return (await self._client.request("GET", "/native/user/subscription")).json()

    async def health(self) -> dict[str, Any]:
        return (await self._client.request("GET", "/health")).json()


class ModelAPI:
    """Model catalogue (sync)."""

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    def list(self) -> list[str]:
        """GET `/v1/models` and return the model IDs."""
        body = self._client.request("GET", "/v1/models").json()
        return [entry["id"] for entry in body.get("data", [])]

    def public_pricing(self) -> dict[str, Any]:
        """GET `/v1/pricing/public`."""
        return self._client.request("GET", "/v1/pricing/public").json()


class AsyncModelAPI:
    """Model catalogue (async)."""

    def __init__(self, client: _AsyncAPIClient) -> None:
        self._client = client

    async def list(self) -> list[str]:
        body = (await self._client.request("GET", "/v1/models")).json()
        return [entry["id"] for entry in body.get("data", [])]

    async def public_pricing(self) -> dict[str, Any]:
        return (await self._client.request("GET", "/v1/pricing/public")).json()


class UploadAPI:
    """Image upload for `upload_id` reuse (sync)."""

    def __init__(self, client: _APIClient) -> None:
        self._client = client

    def image(self, data: bytes, *, filename: str = "image.png", content_type: str = "image/png") -> dict[str, Any]:
        """POST `/v1/upload/image`."""
        response = self._client.request(
            "POST",
            "/v1/upload/image",
            files={"file": (filename, data, content_type)},
        )
        return response.json()


class AsyncUploadAPI:
    """Image upload for `upload_id` reuse (async)."""

    def __init__(self, client: _AsyncAPIClient) -> None:
        self._client = client

    async def image(
        self, data: bytes, *, filename: str = "image.png", content_type: str = "image/png"
    ) -> dict[str, Any]:
        response = await self._client.request(
            "POST",
            "/v1/upload/image",
            files={"file": (filename, data, content_type)},
        )
        return response.json()
