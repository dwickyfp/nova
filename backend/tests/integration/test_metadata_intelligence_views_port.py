"""Semantic View repository reads added for the caller port, on the real PG fixture."""

from __future__ import annotations

import pytest

from tests.integration.test_metadata_intelligence import (  # noqa: F401
    DEFINITION,
    PASSING,
    create_view,
    environment,
    iq,
)

pytestmark = pytest.mark.metadata_pg


async def test_list_versions_returns_every_version_newest_first(iq):  # noqa: F811
    view = await create_view(iq)
    async with iq.transaction() as (repo, _):
        await repo.add_version(view["id"], {**DEFINITION, "label": "two"}, "b" * 64,
                               expected_latest=1)
    async with iq.transaction("task", read_only=True) as (repo, _):
        rows = await repo.list_versions(view["id"])
        assert [row["version"] for row in rows] == [2, 1]
        assert rows[0]["definition"]["label"] == "two"
        assert await repo.list_versions("missing") == []


async def test_link_sources_follow_the_active_or_newest_version(iq):  # noqa: F811
    draft = await create_view(iq, name="draft")
    async with iq.transaction() as (repo, _):
        await repo.add_version(draft["id"], {**DEFINITION, "label": "newest"}, "b" * 64,
                               expected_latest=1)
    published = await create_view(iq, name="published")
    async with iq.transaction() as (repo, _):
        await repo.record_validation(published["id"], 1, PASSING)
        await repo.publish(published["id"], 1, expected_revision=1, expected_active=None)
        await repo.add_version(published["id"], {**DEFINITION, "label": "unpublished"},
                               "c" * 64, expected_latest=1)
    retired = await create_view(iq, name="retired")
    async with iq.transaction() as (repo, _):
        await repo.deprecate(retired["id"], expected_revision=1)

    async with iq.transaction("maintenance", read_only=True) as (repo, _):
        rows = {row["id"]: row for row in await repo.view_link_sources()}
        assert rows[draft["id"]]["version"] == 2
        assert rows[draft["id"]]["definition"]["label"] == "newest"
        assert rows[published["id"]]["version"] == 1
        assert "label" not in rows[published["id"]]["definition"]
        # A deprecated view keeps linking its newest version, as in the source.
        assert rows[retired["id"]]["version"] == 1
        assert rows[draft["id"]]["name"] == "draft"
        assert rows[draft["id"]]["database_name"] == "shop"

        one = await repo.view_link_sources(published["id"])
        assert [row["id"] for row in one] == [published["id"]]
        assert await repo.view_link_sources("missing") == []


async def test_port_reads_need_a_bound_actor(iq):  # noqa: F811
    view = await create_view(iq)
    async with iq.transaction(read_only=True) as (repo, conn):
        await conn.execute("SELECT set_config('nova.principal','someone-else',true)")
        from app.core.metadata_db import MetadataPermissionDenied

        with pytest.raises(MetadataPermissionDenied):
            await repo.list_versions(view["id"])
        with pytest.raises(MetadataPermissionDenied):
            await repo.view_link_sources()
