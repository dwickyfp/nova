"""Dashboard images (P2 part 3): the type comes from the bytes, the object is
written before its registry row and removed when the row fails, an unknown
commit outcome is left to the reconciler, and a damaged copy is never served."""

from __future__ import annotations

import hashlib
import io
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from app.core.deps import get_current_user
from app.core.exceptions import NovaException, StorageError, register_exception_handlers
from app.core.metadata_db import MetadataOutcomeUnknown, MetadataPermissionDenied
from app.modules.viz import assets, assets_router
from app.modules.viz.assets import MAX_BYTES, ImageRefused, NativeAssetService, inspect_image

USER = {"username": "carol", "active_role": "EDITOR", "assigned_roles": ["EDITOR"],
        "session_id": "s", "security_context_version": 1}


def picture(kind="PNG", size=(40, 30), **options):
    out = io.BytesIO()
    Image.new("RGB", size, (12, 120, 200)).save(out, kind, **options)
    return out.getvalue()


# --- sniffing ------------------------------------------------------------------------

@pytest.mark.parametrize(("kind", "media_type", "options"), [
    ("PNG", "image/png", {}),
    ("JPEG", "image/jpeg", {}),
    ("JPEG", "image/jpeg", {"progressive": True}),
    ("WEBP", "image/webp", {}),
    ("WEBP", "image/webp", {"lossless": True}),
])
def test_real_pictures_are_recognised_with_their_size(kind, media_type, options):
    data = picture(kind, (321, 123), **options)
    found = inspect_image(data)
    assert (found.media_type, found.width, found.height) == (media_type, 321, 123)
    assert found.size_bytes == len(data)
    assert found.sha256 == hashlib.sha256(data).hexdigest()


def test_an_extended_webp_reports_its_canvas_size():
    out = io.BytesIO()
    Image.new("RGBA", (250, 90), (0, 0, 0, 0)).save(out, "WEBP")  # alpha → VP8X
    found = inspect_image(out.getvalue())
    assert (found.width, found.height) == (250, 90)


@pytest.mark.parametrize("data", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<?xml version="1.0"?><svg/>',
    picture("GIF"),
    picture("BMP"),
    b"%PDF-1.7\n",
    b"hello",
])
def test_anything_else_is_refused_whatever_it_claims_to_be(data):
    with pytest.raises(ImageRefused) as refused:
        inspect_image(data)
    assert refused.value.status_code == 415
    assert "SVG" in refused.value.message


@pytest.mark.parametrize("data", [
    b"\x89PNG\r\n\x1a\n" + b"\0" * 4,                   # no IHDR
    b"\xff\xd8\xff\xd9",                                # no frame header
    b"RIFF\0\0\0\0WEBPVP8 ",                            # truncated
])
def test_a_damaged_picture_is_refused(data):
    with pytest.raises(ImageRefused) as refused:
        inspect_image(data)
    assert refused.value.status_code == 422


def test_empty_large_and_oversized_pictures_are_refused():
    with pytest.raises(ImageRefused) as empty:
        inspect_image(b"")
    assert empty.value.status_code == 422
    with pytest.raises(ImageRefused) as large:
        inspect_image(b"\x89PNG\r\n\x1a\n" + b"\0" * MAX_BYTES)
    assert large.value.status_code == 413
    huge = bytearray(picture("PNG", (2, 2)))
    huge[16:20] = (9000).to_bytes(4, "big")  # declared width beyond the limit
    with pytest.raises(ImageRefused) as wide:
        inspect_image(bytes(huge))
    assert wide.value.status_code == 422


# --- service -------------------------------------------------------------------------

class Storage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_delete = False

    def internal_object_key(self, namespace, *parts):
        return "/".join(["workspaces", namespace, *parts])

    async def put_internal_object(self, key, data):
        self.objects[key] = data

    async def read_internal_object(self, key):
        if key not in self.objects:
            raise StorageError("missing", status_code=404)
        return self.objects[key]

    async def delete_internal_object(self, key):
        if self.fail_delete:
            raise StorageError("unavailable", status_code=503)
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def world(monkeypatch):
    storage = Storage()
    rows: dict = {}
    repository = SimpleNamespace(
        asset_target=AsyncMock(return_value={"workspace_id": "w1", "owner_name": "owner"}),
        register_failure=None,
    )

    async def register(identity, asset_id, key, found):
        if repository.register_failure:
            raise repository.register_failure
        rows[asset_id] = {"asset_id": asset_id, "object_key": key,
                          "media_type": found.media_type, "size_bytes": found.size_bytes,
                          "content_sha256": found.sha256, "width": found.width,
                          "height": found.height, "created_by": "carol", "created_at": "now"}
        return rows[asset_id]

    async def asset(identity, asset_id):
        if asset_id not in rows:
            raise NovaException("Image not found", status_code=404)
        return rows[asset_id]

    repository.register_asset = register
    repository.asset = asset
    actions: list[tuple[str, bool]] = []

    @asynccontextmanager
    async def transaction(user, *, action, identity=None, read_only=False):
        actions.append((action, read_only))
        yield repository

    monkeypatch.setattr(assets, "document_transaction", transaction)
    monkeypatch.setattr(assets, "_storage", lambda: storage)
    return SimpleNamespace(storage=storage, rows=rows, repository=repository, actions=actions)


async def test_an_upload_writes_the_object_then_registers_it(world):
    board, data = uuid4(), picture("PNG")
    saved = await NativeAssetService().upload(USER, board, data)
    assert set(saved) == {"asset_id", "media_type", "size_bytes", "width", "height", "created_at"}
    (key,) = world.storage.objects
    assert key == f"workspaces/~ws/w1/.viz-assets/{board}/{saved['asset_id'].hex}"
    assert world.rows[saved["asset_id"]]["object_key"] == key
    assert world.actions == [("UPLOAD_IMAGE", True), ("UPLOAD_IMAGE", False)]


async def test_a_personal_dashboard_keeps_its_pictures_in_the_owners_area(world):
    world.repository.asset_target.return_value = {"workspace_id": "personal",
                                                  "owner_name": "owner"}
    await NativeAssetService().upload(USER, uuid4(), picture("PNG"))
    (key,) = world.storage.objects
    assert key.startswith("workspaces/owner/.viz-assets/")


async def test_a_refused_picture_writes_nothing(world):
    with pytest.raises(ImageRefused):
        await NativeAssetService().upload(USER, uuid4(), b"<svg/>")
    assert world.storage.objects == {} and world.actions == []


async def test_a_refused_editor_writes_nothing(world):
    world.repository.asset_target.side_effect = MetadataPermissionDenied()
    with pytest.raises(MetadataPermissionDenied):
        await NativeAssetService().upload(USER, uuid4(), picture("PNG"))
    assert world.storage.objects == {}


async def test_a_failed_registration_removes_the_object(world):
    world.repository.register_failure = MetadataPermissionDenied()
    with pytest.raises(MetadataPermissionDenied):
        await NativeAssetService().upload(USER, uuid4(), picture("PNG"))
    assert world.storage.objects == {} and len(world.storage.deleted) == 1


async def test_a_failed_removal_leaves_the_object_for_the_reconciler(world, caplog):
    world.repository.register_failure = MetadataPermissionDenied()
    world.storage.fail_delete = True
    with pytest.raises(MetadataPermissionDenied):
        await NativeAssetService().upload(USER, uuid4(), picture("PNG"))
    assert len(world.storage.objects) == 1
    assert "reconciler" in caplog.text
    assert "viz-assets" not in caplog.text  # the key is never logged


async def test_an_unknown_commit_outcome_keeps_the_object(world):
    world.repository.register_failure = MetadataOutcomeUnknown()
    with pytest.raises(MetadataOutcomeUnknown):
        await NativeAssetService().upload(USER, uuid4(), picture("PNG"))
    assert len(world.storage.objects) == 1 and world.storage.deleted == []


async def test_a_viewer_reads_the_bytes_with_their_recorded_type(world):
    board, data = uuid4(), picture("WEBP")
    saved = await NativeAssetService().upload(USER, board, data)
    assert await NativeAssetService().read(USER, board, saved["asset_id"]) == (data, "image/webp")
    assert world.actions[-1] == ("READ_IMAGE", True)


async def test_a_missing_or_damaged_copy_is_never_served(world):
    board = uuid4()
    saved = await NativeAssetService().upload(USER, board, picture("PNG"))
    (key,) = world.storage.objects
    world.storage.objects[key] = picture("PNG", (41, 30))  # same type, other bytes
    with pytest.raises(NovaException) as damaged:
        await NativeAssetService().read(USER, board, saved["asset_id"])
    assert damaged.value.status_code == 502
    del world.storage.objects[key]
    with pytest.raises(NovaException) as missing:
        await NativeAssetService().read(USER, board, saved["asset_id"])
    assert missing.value.status_code == 404
    with pytest.raises(NovaException) as unknown:
        await NativeAssetService().read(USER, board, uuid4())
    assert unknown.value.status_code == 404


# --- routes --------------------------------------------------------------------------

@pytest.fixture
async def api(monkeypatch):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(assets_router.router, prefix="/viz")
    app.dependency_overrides[get_current_user] = lambda: USER
    service = SimpleNamespace(
        upload=AsyncMock(return_value={"asset_id": uuid4(), "media_type": "image/png",
                                       "size_bytes": 3, "width": 1, "height": 1,
                                       "created_at": "2026-11-24T00:00:00Z"}),
        read=AsyncMock(return_value=(b"\x89PNG", "image/png")))
    monkeypatch.setattr(assets_router, "native_asset_service", service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        yield client, service


async def test_the_raw_body_is_uploaded_and_never_cached(api):
    client, service = api
    board = uuid4()
    response = await client.post(f"/viz/dashboards/{board}/images", content=b"abc",
                                 headers={"content-type": "image/png"})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["media_type"] == "image/png"
    assert service.upload.await_args.args[1:] == (board, b"abc")


@pytest.mark.parametrize("media_type", ["image/svg+xml", "text/html", "multipart/form-data", ""])
async def test_other_declared_types_are_refused_before_reading(api, media_type):
    client, service = api
    response = await client.post(f"/viz/dashboards/{uuid4()}/images", content=b"<svg/>",
                                 headers={"content-type": media_type})
    assert response.status_code == 415
    service.upload.assert_not_awaited()


async def test_a_body_over_the_limit_is_refused_while_streaming(api):
    client, service = api
    response = await client.post(f"/viz/dashboards/{uuid4()}/images",
                                 content=b"\0" * (MAX_BYTES + 1),
                                 headers={"content-type": "image/png"})
    assert response.status_code == 413
    service.upload.assert_not_awaited()


async def test_a_picture_is_served_with_its_type_and_a_closed_policy(api):
    client, service = api
    board, asset = uuid4(), uuid4()
    response = await client.get(f"/viz/dashboards/{board}/images/{asset}")
    assert response.status_code == 200 and response.content == b"\x89PNG"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert response.headers["cache-control"] == "private, no-store"
    assert service.read.await_args.args[1:] == (board, asset)


async def test_a_refusal_reaches_the_caller(api):
    client, service = api
    service.read.side_effect = NovaException("Document not found", status_code=404)
    assert (await client.get(f"/viz/dashboards/{uuid4()}/images/{uuid4()}")).status_code == 404
    assert (await client.get(f"/viz/dashboards/{uuid4()}/images/not-an-id")).status_code == 422
