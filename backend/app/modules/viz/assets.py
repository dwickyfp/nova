"""Dashboard images: upload, registry and per-request serving (ADR-001, P2 part 3).

An editor uploads a PNG, JPEG or WebP picture for an image item or a page
background. The type comes from the bytes, never from the file name or the
browser's claim, and SVG or anything else is refused. The bytes go to the
dashboard workspace's storage area through the workspace storage owner, under
``.viz-assets/``, before the registry row commits (0064); a failed commit
removes them, and an unknown outcome leaves them for the reconciler, which
keeps an object only while the registry holds its key.

Every read is authorized as the caller against the dashboard (View), checks
that the picture belongs to that dashboard, and verifies the stored bytes
against their recorded digest before serving them.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from app.core.exceptions import NovaException, StorageError
from app.core.metadata_db import MetadataOutcomeUnknown
from app.modules.workspaces.spaces import PERSONAL, namespace_of_space

from .document_service import document_transaction

logger = logging.getLogger(__name__)


def _storage() -> Any:
    """The workspace storage owner (imported late, as the scheduled export does)."""
    from app.modules.workspaces.service import workspace_service

    return workspace_service

MAX_BYTES = 2 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 40_000_000
FOLDER = ".viz-assets"

MediaType = Literal["image/png", "image/jpeg", "image/webp"]


@dataclass(frozen=True)
class Picture:
    media_type: MediaType
    width: int
    height: int
    size_bytes: int
    sha256: str


class ImageRefused(NovaException):
    def __init__(self, message: str, status_code: int = 415) -> None:
        super().__init__(message, status_code=status_code)


_WRONG_TYPE = "Use a PNG, JPEG or WebP picture. Other files, including SVG, are not accepted."


def _png(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ImageRefused("This PNG picture is damaged", status_code=422)
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


# Start-of-frame markers carry the size; C4 (Huffman), C8 and CC are not frames.
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg(data: bytes) -> tuple[int, int]:
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            break
        marker = data[index + 1]
        if marker == 0xFF:  # fill byte
            index += 1
            continue
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:  # no length
            index += 2
            continue
        if marker in {0xD9, 0xDA}:  # end of image, or the scan before any frame header
            break
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if length < 2:
            break
        if marker in _SOF:
            if index + 9 > len(data):
                break
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return width, height
        index += 2 + length
    raise ImageRefused("This JPEG picture is damaged", status_code=422)


def _webp(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF)
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if chunk == b"VP8X" and len(data) >= 30:
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    raise ImageRefused("This WebP picture is damaged", status_code=422)


def inspect_image(data: bytes) -> Picture:
    """The picture's type and size from its bytes; anything else is refused."""
    if not data:
        raise ImageRefused("The file is empty", status_code=422)
    if len(data) > MAX_BYTES:
        raise ImageRefused("Pictures can be up to 2 MB", status_code=413)
    media_type: MediaType
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type, (width, height) = "image/png", _png(data)
    elif data.startswith(b"\xff\xd8\xff"):
        media_type, (width, height) = "image/jpeg", _jpeg(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type, (width, height) = "image/webp", _webp(data)
    else:
        raise ImageRefused(_WRONG_TYPE)
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE) or width * height > MAX_PIXELS:
        raise ImageRefused(
            f"Pictures can be up to {MAX_SIDE} × {MAX_SIDE} pixels and 40 megapixels",
            status_code=422)
    return Picture(media_type, width, height, len(data), hashlib.sha256(data).hexdigest())


def public(row: Mapping[str, Any]) -> dict[str, Any]:
    """What the browser learns about a picture: never where it is stored."""
    return {key: row[key] for key in
            ("asset_id", "media_type", "size_bytes", "width", "height", "created_at")}


async def _discard(key: str) -> None:
    try:
        await _storage().delete_internal_object(key)
    except StorageError:
        # The reconciler removes it after its retention: the registry never held it.
        logger.warning("A dashboard image left after a failed save is left for the reconciler")


class NativeAssetService:
    async def upload(self, user: Mapping[str, Any], dashboard: UUID, data: bytes) -> dict[str, Any]:
        picture = inspect_image(data)
        async with document_transaction(
            user, action="UPLOAD_IMAGE", identity=dashboard, read_only=True,
        ) as repository:
            head = await repository.asset_target(dashboard)
        namespace = (str(head["owner_name"]) if head["workspace_id"] == PERSONAL
                     else namespace_of_space(str(head["workspace_id"])))
        asset_id = uuid4()
        key = _storage().internal_object_key(namespace, FOLDER, str(dashboard), asset_id.hex)
        # The bytes are written before the row that makes them a dashboard image.
        await _storage().put_internal_object(key, data)
        try:
            async with document_transaction(
                user, action="UPLOAD_IMAGE", identity=dashboard,
            ) as repository:
                row = await repository.register_asset(dashboard, asset_id, key, picture)
        except MetadataOutcomeUnknown:
            raise  # The row may have committed: the reconciler decides by key.
        except BaseException:
            await _discard(key)
            raise
        return public(row)

    async def read(self, user: Mapping[str, Any], dashboard: UUID,
                   asset_id: UUID) -> tuple[bytes, str]:
        async with document_transaction(
            user, action="READ_IMAGE", identity=dashboard, read_only=True,
        ) as repository:
            row = await repository.asset(dashboard, asset_id)
        try:
            data = await _storage().read_internal_object(str(row["object_key"]))
        except StorageError as error:
            if error.status_code == 404:
                raise NovaException("Image not found", status_code=404) from None
            raise
        if (len(data) != row["size_bytes"]
                or hashlib.sha256(data).hexdigest() != row["content_sha256"]
                or inspect_image(data).media_type != row["media_type"]):
            raise NovaException("This image's stored copy is damaged", status_code=502)
        return data, str(row["media_type"])


native_asset_service = NativeAssetService()
