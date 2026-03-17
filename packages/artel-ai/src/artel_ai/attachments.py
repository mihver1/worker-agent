"""Helpers for image attachments used by vision-capable providers."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from artel_ai.models import ImageAttachment

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def detect_image_mime_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in _IMAGE_MIME_TYPES:
        return _IMAGE_MIME_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(path)
    if guessed and guessed.startswith("image/"):
        return guessed
    return "application/octet-stream"


def is_supported_image_path(path: str) -> bool:
    return detect_image_mime_type(path).startswith("image/")


def normalize_image_attachment(path: str) -> ImageAttachment:
    resolved = str(Path(path).expanduser().resolve())
    return ImageAttachment(
        path=resolved,
        mime_type=detect_image_mime_type(resolved),
        name=Path(resolved).name,
    )


def attachment_data_base64(attachment: ImageAttachment) -> str:
    data = Path(attachment.path).read_bytes()
    return base64.b64encode(data).decode("ascii")
