"""YesNAI SDK — a Python client for the BestNAI / YesNAI image generation gateway.

Quick start::

    from yesnai import YesNAI
    from yesnai.types import GenerateImageParams

    with YesNAI(api_key="ynai-...", base_url="http://127.0.0.1:8010") as client:
        result = client.image.generate(GenerateImageParams(prompt="1girl, masterpiece"))
        result.images[0].save("out.png")

Low-level requests are supported too, and are forwarded without conversion::

    from yesnai.types.api.image import ImageGenerationRequest, ImageParameters

    request = ImageGenerationRequest(
        model="nai-diffusion-4-5-full",
        action="generate",
        input="1girl",
        parameters=ImageParameters(width=832, height=1216, steps=28),
    )
    client.image.generate(request, native=True)

Streaming yields decoded MessagePack frames::

    for chunk in client.image.stream(GenerateImageParams(prompt="1girl")):
        if chunk.event_type == "final":
            ...

Async equivalents are available as :class:`AsyncYesNAI`.
"""

from ._client.client import (
    AsyncYesNAI,
    GenerationResult,
    ImageGeneration,
    AsyncImageGeneration,
    JsonGenerationResult,
    UploadResult,
    YesNAI,
)
from .exceptions import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    InsufficientBalanceError,
    InvalidRequestError,
    MissingAPIKeyError,
    NetworkError,
    NotFoundError,
    PayloadTooLargeError,
    QueueUnavailableError,
    RateLimitError,
    ServerError,
    StreamError,
    YesNAIError,
)

#: Version of the SDK package. Kept in sync with `pyproject.toml`.
__version__ = "0.1.0"

__all__ = [
    # clients
    "YesNAI",
    "AsyncYesNAI",
    "ImageGeneration",
    "AsyncImageGeneration",
    # results
    "GenerationResult",
    "JsonGenerationResult",
    "UploadResult",
    # errors
    "YesNAIError",
    "MissingAPIKeyError",
    "AuthenticationError",
    "ForbiddenError",
    "InvalidRequestError",
    "NotFoundError",
    "ConflictError",
    "InsufficientBalanceError",
    "PayloadTooLargeError",
    "RateLimitError",
    "QueueUnavailableError",
    "ServerError",
    "NetworkError",
    "StreamError",
    "__version__",
]
