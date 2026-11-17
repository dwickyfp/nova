"""Scheduled dashboard exports on PG17: the task login reads what the role may view
and adds new files to a workspace the role may edit, and nothing else (0053/0054)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.exceptions import NovaException
from app.core.metadata_db import MetadataIntegrityViolation, MetadataPermissionDenied
from app.modules.viz.contracts.commands import GrantsReplace, PublicationRequest
from app.modules.workspaces.metadata_files import WorkspaceFileRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_viz import dashboard, viz  # noqa: F401
from tests.integration.test_metadata_workspaces import session

pytestmark = pytest.mark.metadata_pg

DIGEST = "a" * 64  # any well-formed content digest


def runner(role: str = "EDITOR"):
    """The identity a scheduled run binds: the role's account, a task session."""
    return session("nova_task_service_editor", active=role, session_id="task:run-1")


async def published(viz, *, grant_role: str = "EDITOR", permissions=("view", "export")):  # noqa: F811
    board = dashboard("Weekly sales")
    async with viz.transaction(session("owner")) as (repository, _):
        await repository.create(viz.workspace_id, board, operation_id=uuid4())
        await repository.publish(board.dashboard_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=1))
    async with viz.transaction(session("owner")) as (repository, _):
        await repository.replace_grants(board.dashboard_id, GrantsReplace.model_validate({
            "operation_id": uuid4(), "expected_revision": 1, "grants": [
                {"subject_type": "role", "subject_id": grant_role,
                 "permissions": list(permissions)},
            ],
        }))
    return board


async def test_task_login_reads_a_published_dashboard_its_role_was_granted(viz):  # noqa: F811
    board = await published(viz)
    async with viz.transaction(runner(), workload="task") as (repository, _):
        snapshot = await repository.read(board.dashboard_id)
        assert snapshot["definition"].title == "Weekly sales"
        assert {"view", "export"} <= set(await repository.permissions(board.dashboard_id))


async def test_task_login_sees_nothing_its_role_was_not_granted(viz):  # noqa: F811
    board = await published(viz, grant_role="VIEWER")
    for read in ("permissions", "read"):
        with pytest.raises(NovaException) as hidden:
            async with viz.transaction(runner(), workload="task") as (repository, _):
                await getattr(repository, read)(board.dashboard_id)
        assert hidden.value.status_code == 404


async def test_task_login_never_writes_documents(viz):  # noqa: F811
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(runner(), workload="task") as (repository, _):
            await repository.create(viz.workspace_id, dashboard("Forged"), operation_id=uuid4())


async def test_task_login_adds_a_file_only_where_the_role_may_edit(viz):  # noqa: F811
    namespace = "~ws/" + viz.workspace_id
    async with viz.transaction(runner(), workload="task",
                               repository_type=WorkspaceFileRepository) as (files, _):
        folder = await files.insert_entry(namespace, parent_path="", name="Scheduled exports",
                                          entry_type="folder", object_key=None)
        entry = await files.insert_entry(namespace, parent_path="Scheduled exports",
                                         name="Weekly sales.xlsx", entry_type="file",
                                         object_key="viz-export/" + uuid4().hex,
                                         size_bytes=10, etag="etag", content_sha256=DIGEST)
        assert (folder["path"], entry["path"]) == (
            "Scheduled exports", "Scheduled exports/Weekly sales.xlsx")
    # A view-only share, and anyone's personal files, are refused.
    for user, target in ((runner("VIEWER"), namespace), (runner(), "owner")):
        with pytest.raises((MetadataPermissionDenied, MetadataIntegrityViolation)):
            async with viz.transaction(user, workload="task",
                                       repository_type=WorkspaceFileRepository) as (files, _):
                await files.insert_entry(target, parent_path="", name="x.xlsx",
                                         entry_type="file", object_key="k-" + uuid4().hex,
                                         size_bytes=1, etag="e", content_sha256=DIGEST)


async def test_task_login_cannot_change_or_remove_files(viz):  # noqa: F811
    namespace = "~ws/" + viz.workspace_id
    async with viz.transaction(runner(), workload="task",
                               repository_type=WorkspaceFileRepository) as (files, _):
        entry = await files.insert_entry(namespace, parent_path="", name="report.xlsx",
                                         entry_type="file", object_key="k-" + uuid4().hex,
                                         size_bytes=1, etag="e", content_sha256=DIGEST)
    for change in (
        "UPDATE nova_meta.config_workspace_entries SET name='renamed.xlsx',"
        "metadata_revision=metadata_revision+1 WHERE id=%s",
        "DELETE FROM nova_meta.config_workspace_entries WHERE id=%s",
    ):
        with pytest.raises(MetadataPermissionDenied):
            async with viz.transaction(runner(), workload="task",
                                       repository_type=WorkspaceFileRepository) as (_, conn):
                await conn.execute(change, (entry["id"],))
    rows = await viz.inspect(
        "SELECT name,is_deleted FROM nova_meta.config_workspace_entries WHERE id=%s",
        (entry["id"],))
    assert rows == [{"name": "report.xlsx", "is_deleted": False}]


async def test_a_scheduled_export_file_is_private_to_the_workspace(viz):  # noqa: F811
    """NDV-STORE-008: there is no public or signed link to an exported workbook; the
    file is a workspace entry, so only roles the workspace is shared with can find
    it, and the storage key never reaches anyone who cannot."""
    namespace = "~ws/" + viz.workspace_id
    async with viz.transaction(runner(), workload="task",
                               repository_type=WorkspaceFileRepository) as (files, _):
        await files.insert_entry(namespace, parent_path="", name="Scheduled exports",
                                 entry_type="folder", object_key=None)
        entry = await files.insert_entry(namespace, parent_path="Scheduled exports",
                                         name="Weekly sales.xlsx", entry_type="file",
                                         object_key="viz-export/" + uuid4().hex,
                                         size_bytes=10, etag="etag", content_sha256=DIGEST)
    # A role the workspace is shared with finds it.
    async with viz.transaction(session("viewer-person", active="VIEWER"),
                               repository_type=WorkspaceFileRepository) as (files, _):
        assert (await files.get_entry(namespace, entry["id"]))["name"] == "Weekly sales.xlsx"
    # Another role of a member, and another person, find nothing: not by listing,
    # not by id, not by asking which workspace the id belongs to.
    for outsider in (session("viewer-person", active="OUTSIDERS"),
                     session("stranger", active="OUTSIDERS")):
        async with viz.transaction(outsider,
                                   repository_type=WorkspaceFileRepository) as (files, _):
            assert await files.list_entries(namespace) == []
            assert await files.get_entry(namespace, entry["id"]) is None
            assert await files.namespace_of(entry["id"]) is None
