"""Release QA scenarios for native documents on PG17: refused writes leave no
trace, races have one consistent outcome, and deletion is audited."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.core.metadata_db import MetadataConflict, MetadataPermissionDenied
from app.modules.viz.contracts.commands import DeleteRequest, PublicationRequest
from app.modules.viz.contracts.dashboards import ChartReference, Geometry, VisualElement
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_viz import (  # noqa: F401
    create,
    dashboard,
    grant,
    http_headers,
    native_http,
    native_service,
    viz,
)
from tests.integration.test_metadata_workspaces import session
from tests.unit.test_native_viz_bindings import chart, dataset

pytestmark = pytest.mark.metadata_pg


async def counts(viz):  # noqa: F811
    rows = await viz.inspect(
        "SELECT (SELECT count(*) FROM nova_meta.viz_revisions) AS revisions,"
        "(SELECT count(*) FROM nova_meta.viz_outbox) AS events,"
        "(SELECT count(*) FROM nova_meta.viz_operations) AS operations")
    return rows[0]


async def test_a_viewer_cannot_change_a_dashboard_and_leaves_no_trace(viz, native_http):  # noqa: F811
    """NDV-ACL-008, NDV-REVIS-012: a view-only reader edits the draft or posts an operation."""
    client, records = native_http
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    await grant(viz, identity, {"subject_type": "user", "subject_id": "reader",
                                "permissions": ["view", "interact"]})
    before = await counts(viz)
    reader = records["login-reader"]
    path = f"/api/v1/viz/dashboards/{identity}"
    renamed = document.model_copy(update={"title": "Hijacked"}).model_dump(mode="json")
    draft = await client.patch(path + "/draft", json=renamed,
                               headers=http_headers(reader, key=uuid4(), revision=1))
    key = uuid4()
    operation = await client.post(path + "/operations", headers=http_headers(reader, key=key,
                                                                              revision=1),
                                  json={"operation_id": str(key), "expected_revision": 1,
                                        "changes": [{"op": "dashboard.rename", "title": "X"}]})
    assert draft.status_code in {403, 404} and operation.status_code in {403, 404}
    assert "Hijacked" not in draft.text
    assert await counts(viz) == before


async def test_a_guessed_chart_id_cannot_be_edited(viz, native_http):  # noqa: F811
    """NDV-ACL-007: PATCH a chart the caller cannot see; nothing is written."""
    client, records = native_http
    source = dataset()
    foreign = chart(source, dataset_revision=1)
    async with viz.transaction(session("mallory")) as (repository, _):
        await repository.create("personal", source, operation_id=uuid4())
    async with viz.transaction(session("mallory")) as (repository, _):
        await repository.create("personal", foreign, operation_id=uuid4())
    before = await counts(viz)
    response = await client.patch(
        f"/api/v1/viz/charts/{foreign.chart_id}/draft",
        json=foreign.model_copy(update={"title": "Taken"}).model_dump(mode="json"),
        headers=http_headers(records["login-owner"], key=uuid4(), revision=1))
    assert response.status_code == 404
    assert await counts(viz) == before


async def test_a_guessed_dataset_id_reveals_nothing(viz, native_http):  # noqa: F811
    """NDV-ACL-006: query and read a dataset id that exists for someone else."""
    client, records = native_http
    source = dataset()
    async with viz.transaction(session("mallory")) as (repository, _):
        await repository.create("personal", source, operation_id=uuid4())
    headers = http_headers(records["login-owner"])
    read = await client.get(f"/api/v1/viz/datasets/{source.dataset_id}?draft=true",
                            headers=headers)
    assert read.status_code == 404
    assert source.title not in read.text and "orders" not in read.text
    query = await client.post(f"/api/v1/viz/datasets/{source.dataset_id}/query", headers=headers,
                              json={"revision": 1, "draft": True,
                                    "measures": [{"field_id": "amount", "aggregation": "sum"}]})
    assert query.status_code in {403, 404}
    assert "orders" not in query.text and "amount" not in query.text


async def test_an_editor_without_publish_leaves_the_published_version_as_it_was(viz):  # noqa: F811
    """NDV-ACL-010: publish once, then an editor's publish is refused."""
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    async with viz.transaction() as (repository, _):
        await repository.publish(identity, PublicationRequest(operation_id=uuid4(),
                                                              expected_revision=1))
    await grant(viz, identity, {"subject_type": "user", "subject_id": "editor",
                                "permissions": ["view", "interact", "comment", "edit"]})
    editor = session("editor", active="EDITOR")
    async with viz.transaction(editor) as (repository, _):
        await repository.replace(document.model_copy(update={"title": "Draft only"}),
                                 operation_id=uuid4(), expected_revision=1)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(editor) as (repository, _):
            await repository.publish(identity, PublicationRequest(operation_id=uuid4(),
                                                                  expected_revision=2))
    async with viz.transaction() as (repository, _):
        published = await repository.read(identity)
    assert published["revision"] == 1 and published["definition"].title == document.title


async def test_publish_racing_an_edit_publishes_one_consistent_revision(viz):  # noqa: F811
    """NDV-REVIS-003: publish(expected 1) and an edit(expected 1) at the same moment."""
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    barrier = asyncio.Barrier(2)

    async def publish():
        try:
            async with viz.transaction() as (repository, _):
                await barrier.wait()
                return await repository.publish(identity, PublicationRequest(
                    operation_id=uuid4(), expected_revision=1))
        except MetadataConflict:
            return "conflict"

    async def edit():
        try:
            async with viz.transaction() as (repository, _):
                await barrier.wait()
                return await repository.replace(document.model_copy(update={"title": "Edited"}),
                                                operation_id=uuid4(), expected_revision=1)
        except MetadataConflict:
            return "conflict"

    results = await asyncio.gather(publish(), edit())
    async with viz.transaction() as (repository, _):
        head = await repository.read(identity, draft=True)
        try:
            live = await repository.read(identity)
        except Exception:  # noqa: BLE001 - not published when publish lost the race
            live = None
    if live is not None:
        # Whatever is published is exactly revision 1, never a mix with the edit.
        assert live["revision"] == 1 and live["definition"].title == document.title
    assert results.count("conflict") <= 1
    assert head["revision"] in {1, 2}
    if head["revision"] == 2:
        assert head["definition"].title == "Edited"


async def test_two_dataset_edits_have_one_winner(viz):  # noqa: F811
    """NDV-DATA-013: the same dataset revision edited from two clients."""
    source = dataset()
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
    barrier = asyncio.Barrier(2)

    async def edit(title):
        try:
            async with viz.transaction() as (repository, _):
                await barrier.wait()
                return await repository.replace(source.model_copy(update={"title": title}),
                                                operation_id=uuid4(), expected_revision=1)
        except MetadataConflict:
            return "conflict"

    results = await asyncio.gather(edit("First"), edit("Second"))
    assert results.count("conflict") == 1
    async with viz.transaction() as (repository, _):
        head = await repository.read(source.dataset_id, draft=True)
    assert head["revision"] == 2 and head["definition"].title in {"First", "Second"}


async def test_a_widget_cannot_point_at_a_chart_the_editor_cannot_see(viz):  # noqa: F811
    """NDV-GRID-010: add a widget whose chart belongs to someone else's private space."""
    source = dataset()
    foreign = chart(source, dataset_revision=1)
    async with viz.transaction(session("mallory")) as (repository, _):
        await repository.create("personal", source, operation_id=uuid4())
    async with viz.transaction(session("mallory")) as (repository, _):
        await repository.create("personal", foreign, operation_id=uuid4())
    document, _, _ = await create(viz)
    page = document.pages[0]
    widget = VisualElement(element_id=uuid4(), geometry=Geometry(x=24, y=24, w=600, h=280),
                           chart_ref=ChartReference(chart_id=foreign.chart_id, revision=1))
    pages = (page.model_copy(update={"elements": (widget,)}),)
    changed = document.model_copy(update={"pages": pages})
    before = await counts(viz)
    with pytest.raises((MetadataPermissionDenied, ValueError, Exception)) as refused:
        async with viz.transaction() as (repository, _):
            await repository.replace(changed, operation_id=uuid4(), expected_revision=1)
    assert not isinstance(refused.value, AssertionError)
    assert await counts(viz) == before


async def test_deleting_a_dashboard_is_audited_and_hides_it(viz):  # noqa: F811
    """NDV-ACL-022: soft delete keeps history, writes an audit row, and hides the document."""
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    async with viz.transaction() as (repository, _):
        await repository.delete(identity, "dashboard", DeleteRequest(operation_id=uuid4(),
                                                                     expected_revision=1))
    audit = await viz.inspect(
        "SELECT action, status FROM nova_meta.audit_log WHERE object_type='viz_document' "
        "AND object_name=%s ORDER BY event_time", (str(identity),))
    assert [row["action"] for row in audit][-1] == "DELETE"
    kept = await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_revisions "
                             "WHERE resource_id=%s", (identity,))
    assert kept == [{"n": 1}]
    with pytest.raises(Exception):  # noqa: B017 - not found for everyone once deleted
        async with viz.transaction() as (repository, _):
            await repository.read(identity, draft=True)
