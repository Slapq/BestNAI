"""In-memory image store with SHA-256 dedup and normalization."""
from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from typing import Any

from PIL import Image


DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_STORE_ENTRIES = 500


@dataclass(slots=True)
class StoredImage:
    upload_id: str
    sha256: str
    data_b64: str
    width: int
    height: int
    size_bytes: int
    mime_type: str = "image/png"


class ImageStore:
    def __init__(self, max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES, max_entries: int = DEFAULT_MAX_STORE_ENTRIES):
        self.max_upload_bytes = max_upload_bytes
        self.max_entries = max_entries
        self._store: dict[str, StoredImage] = {}
        self._sha_index: dict[str, str] = {}

    def configure(self, *, max_upload_bytes: int | None = None, max_entries: int | None = None) -> None:
        if max_upload_bytes is not None:
            self.max_upload_bytes = max_upload_bytes
        if max_entries is not None:
            self.max_entries = max_entries

    def put(self, raw: bytes, mime_hint: str = "image/png") -> StoredImage:
        if len(raw) > self.max_upload_bytes:
            raise ValueError(f"Image exceeds {self.max_upload_bytes} bytes upload limit")

        sha = hashlib.sha256(raw).hexdigest()
        existing_uid = self._sha_index.get(sha)
        if existing_uid:
            return self._store[existing_uid]

        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()
        data_b64 = base64.b64encode(png_bytes).decode("utf-8")

        uid = sha[:16]
        entry = StoredImage(
            upload_id=uid,
            sha256=sha,
            data_b64=data_b64,
            width=img.width,
            height=img.height,
            size_bytes=len(png_bytes),
            mime_type="image/png" if mime_hint.startswith("image/") else mime_hint,
        )

        if len(self._store) >= self.max_entries:
            oldest_uid = next(iter(self._store))
            oldest = self._store.pop(oldest_uid)
            self._sha_index.pop(oldest.sha256, None)

        self._store[uid] = entry
        self._sha_index[sha] = uid
        return entry

    def get_b64(self, ref: str) -> str:
        if ref.startswith("upload_id:"):
            uid = ref.removeprefix("upload_id:")
            if uid not in self._store:
                raise KeyError(f"Unknown upload_id: {uid}")
            return self._store[uid].data_b64
        return ref

    def resolve_in_object(self, obj: Any) -> Any:
        """Recursively resolve upload_id: refs inside dict/list structures."""
        if isinstance(obj, dict):
            return {k: self.resolve_in_object(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.resolve_in_object(v) for v in obj]
        if isinstance(obj, str):
            return self.get_b64(obj) if obj.startswith("upload_id:") else obj
        return obj

    def get_entry(self, uid: str) -> StoredImage:
        if uid not in self._store:
            raise KeyError(f"Unknown upload_id: {uid}")
        return self._store[uid]


image_store = ImageStore()
