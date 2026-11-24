"""Dashboard images on PG17 (0064): Edit registers a picture as the uploader,
View reads it, nobody else learns it exists, a row never changes, every
registration is audited, and the object reconciler sees keys and nothing else."""

from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import pytest

from app.core.exceptions import NovaException
from app.core.metadata_db import (
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataPermissionDenied,
)
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.viz.assets import Picture
from app.modules.workspaces.object_reconciler import RECONCILER, referenced_in
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_viz import viz  # noqa: F401
from tests.integration.test_metadata_viz_comments import person, shared
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg

OWNER_SESSION = session("owner")
PICTURE = Picture("image/png", 640, 480, 1234, "a" * 64)


def object_key(board, asset):
    return f"workspaces/~ws/w1/.viz-assets/{board}/{asset.hex}"


async def uploaded(viz, board, who="carol"):  # noqa: F811
    asset = uuid4()
    async with viz.transaction(person(who)) as (repository, _):
        row = await repository.register_asset(board, asset, object_key(board, asset), PICTURE)
    return asset, row


async def test_an_editor_registers_and_a_viewer_reads(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "edit"], "vera": ["view"]})
    asset, row = await uploaded(viz, board)
    assert row["created_by"] == "carol" and row["media_type"] == "image/png"
    assert (row["width"], row["height"], row["size_bytes"]) == (640, 480, 1234)
    async with viz.transaction(person("vera")) as (repository, _):
        seen = await repository.asset(board, asset)
        assert seen["object_key"] == object_key(board, asset)
        assert seen["content_sha256"] == "a" * 64


async def test_a_viewer_cannot_register_and_a_stranger_learns_nothing(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "edit"], "vera": ["view"]})
    asset, _ = await uploaded(viz, board)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(person("vera")) as (repository, _):
            await repository.register_asset(board, uuid4(), object_key(board, uuid4()), PICTURE)
    with pytest.raises(NovaException) as refused:
        async with viz.transaction(person("mallory")) as (repository, _):
            await repository.asset(board, asset)
    assert refused.value.status_code == 404
    # Row security alone hides the row, even from a direct query.
    async with viz.transaction(person("mallory")) as (_, conn):
        cursor = await conn.execute("SELECT asset_id FROM nova_meta.viz_assets")
        assert await cursor.fetchall() == []


async def test_a_picture_of_another_dashboard_is_not_found(viz):  # noqa: F811
    first = await shared(viz, {"carol": ["view", "edit"]})
    second = await shared(viz, {"carol": ["view", "edit"]})
    asset, _ = await uploaded(viz, first)
    with pytest.raises(NovaException) as refused:
        async with viz.transaction(person("carol")) as (repository, _):
            await repository.asset(second, asset)
    assert refused.value.status_code == 404


async def test_the_uploader_cannot_be_forged_and_a_row_never_changes(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "edit"]})
    asset, _ = await uploaded(viz, board)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(person("carol")) as (_, conn):
            other = uuid4()
            await conn.execute(
                "INSERT INTO nova_meta.viz_assets (asset_id,resource_id,object_key,media_type,"
                "size_bytes,content_sha256,width,height,created_by) "
                "VALUES (%s,%s,%s,'image/png',1,%s,1,1,'owner')",
                (other, board, object_key(board, other), "b" * 64))
    for change in ("UPDATE nova_meta.viz_assets SET width=1 WHERE asset_id=%s",
                   "DELETE FROM nova_meta.viz_assets WHERE asset_id=%s"):
        with pytest.raises(MetadataPermissionDenied):
            async with viz.transaction(OWNER_SESSION) as (_, conn):
                await conn.execute(change, (asset,))
    rows = await viz.inspect("SELECT width FROM nova_meta.viz_assets WHERE asset_id=%s", (asset,))
    assert rows == [{"width": 640}]


async def test_a_key_outside_the_image_folder_or_a_wrong_type_is_refused(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "edit"]})
    asset = uuid4()
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction(person("carol")) as (repository, _):
            await repository.register_asset(board, asset, "workspaces/carol/notes.sql", PICTURE)
    svg = Picture("image/svg+xml", 1, 1, 10, "c" * 64)  # type: ignore[arg-type]
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction(person("carol")) as (repository, _):
            await repository.register_asset(board, asset, object_key(board, asset), svg)
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_assets") == [{"n": 0}]


async def test_every_registration_is_audited_without_the_picture(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "edit"]})
    asset, _ = await uploaded(viz, board)
    rows = await viz.inspect(
        "SELECT action, object_name, metadata_key FROM nova_meta.audit_log "
        "WHERE object_type='viz_asset'")
    assert rows == [{"action": "UPLOAD_IMAGE", "object_name": str(board), "metadata_key": {
        "asset_id": str(asset), "media_type": "image/png", "size_bytes": 1234}}]


@asynccontextmanager
async def maintenance(viz, actor):  # noqa: F811
    db = MetadataDatabase(viz.environment.role_config("nova_meta_maintenance"))
    try:
        await db.open(schema_version=len(load_migrations()), metadata_epoch=viz.epoch)
        async with db.transaction(actor, read_only=True) as conn:
            yield conn
    finally:
        await db.close()


async def test_the_reconciler_sees_image_keys_and_nothing_else(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "edit"]})
    asset, _ = await uploaded(viz, board)
    held, orphan = object_key(board, asset), object_key(board, uuid4())
    async with maintenance(viz, RECONCILER) as conn:
        assert await referenced_in(conn, [held, orphan]) == {held}
    with pytest.raises(MetadataPermissionDenied):
        async with maintenance(viz, RECONCILER) as conn:
            await conn.execute("SELECT content_sha256 FROM nova_meta.viz_assets LIMIT 1")
    other = SecurityContext("catalog-refresher", "SECURITYADMIN", principal_type="SERVICE")
    async with maintenance(viz, other) as conn:
        assert await referenced_in(conn, [held]) == set()


async def test_other_workloads_cannot_touch_images(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "edit"]})
    for workload in ("query", "task"):
        with pytest.raises(MetadataPermissionDenied):
            async with viz.transaction(person("carol"), workload=workload) as (_, conn):
                await conn.execute("SELECT 1 FROM nova_meta.viz_assets WHERE resource_id=%s",
                                   (board,))

