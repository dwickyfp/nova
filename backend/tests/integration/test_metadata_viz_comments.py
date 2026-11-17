"""Dashboard comments on PG17 (0055): View reads, Comment writes and resolves,
the author or a manager removes, text never changes, every change is audited."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.exceptions import NovaException
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.viz.contracts.commands import GrantsReplace
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_viz import dashboard, viz  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg

OWNER = session("owner")


async def shared(viz, grants):  # noqa: F811
    board = dashboard("Weekly sales")
    async with viz.transaction(OWNER) as (repository, _):
        await repository.create(viz.workspace_id, board, operation_id=uuid4())
    async with viz.transaction(OWNER) as (repository, _):
        await repository.replace_grants(board.dashboard_id, GrantsReplace.model_validate({
            "operation_id": uuid4(), "expected_revision": 1, "grants": [
                {"subject_type": "user", "subject_id": name, "permissions": permissions}
                for name, permissions in grants.items()]}))
    return board.dashboard_id


def person(name):
    return session(name, active="EDITOR")


async def test_commenters_post_and_viewers_read_but_cannot_post(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "comment"], "vera": ["view"]})
    note = uuid4()
    async with viz.transaction(person("carol")) as (repository, _):
        saved = await repository.add_comment(board, note, "Check the Bandung dip",
                                             page_id=None, widget_id=None)
        assert saved["author"] == "carol" and saved["resolved_at"] is None and saved["mine"]
        # A retried post returns the same comment.
        again = await repository.add_comment(board, note, "Check the Bandung dip",
                                             page_id=None, widget_id=None)
        assert again["comment_id"] == note
    async with viz.transaction(person("vera")) as (repository, _):
        listed = await repository.comments(board)
        assert [row["body"] for row in listed] == ["Check the Bandung dip"]
        assert listed[0]["mine"] is False
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(person("vera")) as (repository, _):
            await repository.add_comment(board, uuid4(), "Me too", page_id=None, widget_id=None)
    with pytest.raises(NovaException):
        async with viz.transaction(person("mallory")) as (repository, _):
            await repository.comments(board)


async def test_a_comment_cannot_be_forged_or_rewritten(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "comment"]})
    note = uuid4()
    async with viz.transaction(person("carol")) as (repository, _):
        await repository.add_comment(board, note, "Original", page_id=None, widget_id=None)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(person("carol")) as (_, conn):
            await conn.execute("INSERT INTO nova_meta.viz_comments "
                               "(comment_id,resource_id,author,body) VALUES (%s,%s,'owner','x')",
                               (uuid4(), board))
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(person("carol")) as (_, conn):
            await conn.execute("UPDATE nova_meta.viz_comments SET body='Changed' "
                               "WHERE comment_id=%s", (note,))
    rows = await viz.inspect("SELECT body FROM nova_meta.viz_comments WHERE comment_id=%s", (note,))
    assert rows == [{"body": "Original"}]


async def test_resolve_and_reopen_record_who_and_when(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "comment"], "dan": ["view", "comment"]})
    note = uuid4()
    async with viz.transaction(person("carol")) as (repository, _):
        await repository.add_comment(board, note, "Fix the axis", page_id=None, widget_id=None)
    async with viz.transaction(person("dan")) as (repository, _):
        resolved = await repository.resolve_comment(board, note, True)
        assert resolved["resolved_by"] == "dan" and resolved["resolved_at"] is not None
        reopened = await repository.resolve_comment(board, note, False)
        assert reopened["resolved_by"] is None and reopened["resolved_at"] is None


async def test_only_the_author_or_a_manager_removes_a_comment(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "comment"], "dan": ["view", "comment"]})
    first, second = uuid4(), uuid4()
    async with viz.transaction(person("carol")) as (repository, _):
        await repository.add_comment(board, first, "One", page_id=None, widget_id=None)
        await repository.add_comment(board, second, "Two", page_id=None, widget_id=None)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(person("dan")) as (repository, _):
            await repository.delete_comment(board, first)
    async with viz.transaction(person("carol")) as (repository, _):
        await repository.delete_comment(board, first)
    async with viz.transaction(OWNER) as (repository, _):  # the owner manages the dashboard
        await repository.delete_comment(board, second)
        assert await repository.comments(board) == []
    kept = await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_comments "
                             "WHERE resource_id=%s AND deleted_at IS NOT NULL", (board,))
    assert kept == [{"n": 2}]
    actions = await viz.inspect("SELECT action FROM nova_meta.audit_log "
                                "WHERE object_type='viz_comment' ORDER BY event_time")
    assert [row["action"] for row in actions] == ["COMMENT", "COMMENT", "DELETE_COMMENT",
                                                  "DELETE_COMMENT"]


async def test_other_workloads_cannot_touch_comments(viz):  # noqa: F811
    board = await shared(viz, {"carol": ["view", "comment"]})
    for workload in ("query", "task"):
        with pytest.raises(MetadataPermissionDenied):
            async with viz.transaction(person("carol"), workload=workload) as (_, conn):
                await conn.execute("SELECT 1 FROM nova_meta.viz_comments WHERE resource_id=%s",
                                   (board,))
