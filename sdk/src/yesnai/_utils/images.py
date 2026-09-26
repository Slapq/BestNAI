"""Image and stream decoding helpers."""
from __future__ import annotations

import base64
import io
import json
import struct
import zipfile
from typing import Any, Iterator

import msgpack
from PIL import Image

#: 4-byte big-endian length prefix used by the MessagePack stream relays.
FRAME_HEADER_SIZE = 4
MAX_FRAME_BYTES = 192 * 1024 * 1024


def decode_base64_image(value: str) -> Image.Image:
    """Decode a base64 PNG/WebP payload into a PIL image."""
    data = value
    if data.startswith("data:"):
        _, _, data = data.partition(",")
    raw = base64.b64decode(data)
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image


def extract_images_from_zip(payload: bytes) -> list[Image.Image]:
    """Extract every image from a ZIP response, in archive order."""
    images: list[Image.Image] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            if name.endswith("/") or name == "metadata.json":
                continue
            try:
                image = Image.open(io.BytesIO(archive.read(name)))
                image.load()
            except Exception:
                continue
            images.append(image)
    return images


def extract_metadata_from_zip(payload: bytes) -> dict[str, Any] | None:
    """Read the optional `metadata.json` member from a ZIP response."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if "metadata.json" not in archive.namelist():
                return None
            return json.loads(archive.read("metadata.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        return None


def iter_msgpack_frames(chunks: Iterator[bytes]) -> Iterator[dict[str, Any]]:
    """Decode a `4-byte length + MessagePack` stream.

    The gateway relays upstream frames verbatim, so a chunk boundary never
    carries meaning: frames are reassembled from a running buffer.
    """
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        while True:
            if len(buffer) < FRAME_HEADER_SIZE:
                break
            length = struct.unpack(">I", bytes(buffer[:FRAME_HEADER_SIZE]))[0]
            if length > MAX_FRAME_BYTES:
                raise ValueError(f"Stream frame length {length} exceeds the supported maximum")
            if len(buffer) < FRAME_HEADER_SIZE + length:
                break
            payload = bytes(buffer[FRAME_HEADER_SIZE : FRAME_HEADER_SIZE + length])
            del buffer[: FRAME_HEADER_SIZE + length]
            try:
                frame = msgpack.unpackb(payload, raw=False)
            except Exception as exc:
                raise ValueError("Stream frame was not valid MessagePack") from exc
            if isinstance(frame, dict):
                yield frame


def pack_frame(frame: dict[str, Any]) -> bytes:
    """Encode one frame with the length prefix (useful for tests and fixtures)."""
    payload = msgpack.packb(frame, use_bin_type=True)
    return struct.pack(">I", len(payload)) + payload


def iter_sse_data(chunks: Iterator[bytes]) -> Iterator[dict[str, Any]]:
    """Decode a `data: {...}` SSE stream into JSON objects."""
    buffer = ""
    for chunk in chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in buffer:
            block, _, buffer = buffer.partition("\n\n")
            for line in block.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    parsed = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    yield parsed


def image_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt.upper())
    return buffer.getvalue()
