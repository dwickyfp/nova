"""Native document isolation, immutable publications and atomic revision races on PG17."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from pydantic import ValidationError

from app.core import metadata_db as metadata_db_module
from app.core import metadata_scope, security
from app.core.config import settings
from app.core.exceptions import NovaException, register_exception_handlers
from app.core.metadata_db import (
    MetadataConflict,
    MetadataDatabase,
    MetadataIntegrityViolation,
    MetadataOperationFailed,
    MetadataOutcomeUnknown,
    MetadataPermissionDenied,
)
from app.core.metadata_migrations import migrate
from app.core.metadata_schema import load_migrations
from app.modules.access_control.security_context import SecurityContext
from app.modules.intelligence.metadata_repository import IntelligenceMetadataRepository
from app.modules.viz import document_service
from app.modules.viz import router as viz_router
from app.modules.viz.contracts.authoring import (
    DashboardDatasetCreate,
    DatasetPreviewRequest,
    ObjectDatasetCreate,
    ObjectDatasetUpdate,
)
from app.modules.viz.contracts.commands import (
    DeleteRequest,
    GrantsReplace,
    PublicationRequest,
    RestoreRequest,
)
from app.modules.viz.contracts.dashboards import DashboardDefinition, DatasetReference, Page
from app.modules.viz.contracts.datasets import SqlSource
from app.modules.viz.contracts.filters import FilterDefinition
from app.modules.viz.dataset_service import NativeDatasetService
from app.modules.viz.document_service import NativeDocumentService
from app.modules.viz.documents import VizDocumentRepository
from app.modules.workspaces.metadata_sharing import WorkspaceSharingRepository
from tests.integration.test_metadata_bootstrap import environment  # noqa: F401
from tests.integration.test_metadata_workspaces import session
from tests.unit.test_native_viz_bindings import chart, dataset

pytestmark = pytest.mark.metadata_pg


async def test_sensitive_parameter_default_cannot_be_saved_through_dashboard_control(viz):
    source = dataset(parameters=[{"parameter_id": "private_input", "type": "string",
                                  "sensitive": True}])
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
    document = dashboard()
    control = FilterDefinition.model_validate({
        "filter_id": uuid4(), "title": "Input", "kind": "parameter_control",
        "parameter": {"dataset_id": source.dataset_id, "dataset_revision": 1,
                      "parameter_id": "private_input"},
        "scope": {"kind": "global"},
        "defaults": [{"type": "string", "value": "private-test-input"}],
    })
    document = document.model_copy(update={"filters": (control,)})
    with pytest.raises(ValueError, match="Sensitive parameter defaults"):
        async with viz.transaction() as (repository, _):
            await repository.create(viz.workspace_id, document, operation_id=uuid4())
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 1}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 1}]


class VizFixture:
    def __init__(self, fixture_environment, epoch, workspace_id, schema_version=None):
        self.environment = fixture_environment
        self.epoch = epoch
        self.workspace_id = workspace_id
        self.schema_version = schema_version or len(load_migrations())

    @asynccontextmanager
    async def transaction(
        self, user=None, *, workload="web", repository_type=VizDocumentRepository,
    ):
        user = user or session("owner")
        database = MetadataDatabase(self.environment.role_config("nova_meta_" + workload))
        await database.open(schema_version=self.schema_version, metadata_epoch=self.epoch)
        try:
            async with (
                database.transaction(SecurityContext.from_session(user)) as connection,
                repository_type.bind(connection, user) as repository,
            ):
                yield repository, connection
        finally:
            await database.close()

    async def inspect(self, sql, params=()):
        cursor = await self.environment.admin.execute(sql, params)
        return await cursor.fetchall()


@pytest_asyncio.fixture
async def viz(environment):  # noqa: F811
    await environment.bootstrap()
    await migrate(environment.role_config("nova_meta_migrator"), app_version="d" * 40)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    fixture = VizFixture(environment, epoch, None)
    async with fixture.transaction(repository_type=WorkspaceSharingRepository) as (spaces, _):
        workspace = await spaces.create("Native BI test")
        await spaces.replace_shares(workspace["id"], {"VIEWER": "view", "EDITOR": "edit"},
                                   expected_revision=1)
    fixture.workspace_id = workspace["id"]
    return fixture


def dashboard(title="Sales"):
    return DashboardDefinition(dashboard_id=uuid4(), title=title,
                               pages=(Page(page_id=uuid4(), title="Page 1"),))


async def test_dashboard_dataset_registry_pins_and_protects_unvisualized_data(viz):
    source = dataset()
    board = dashboard().model_copy(update={
        "datasets": (DatasetReference(dataset_id=source.dataset_id, revision=1),),
    })
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
        await repository.create(viz.workspace_id, board, operation_id=uuid4())
        await repository.publish(board.dashboard_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (repository, _):
            await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                operation_id=uuid4(), expected_revision=1,
            ))
    async with viz.transaction() as (repository, _):
        await repository.replace(source.model_copy(update={"title": "New source draft"}),
                                 operation_id=uuid4(), expected_revision=1)
        published = await repository.read(board.dashboard_id)
        assert published["definition"].datasets[0].revision == 1
        await repository.replace(board.model_copy(update={"datasets": ()}),
                                 operation_id=uuid4(), expected_revision=1)
        await repository.publish(board.dashboard_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=2,
        ))
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (repository, _):
            await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                operation_id=uuid4(), expected_revision=2,
            ))
    async with viz.transaction() as (repository, _):
        await repository.delete(board.dashboard_id, "dashboard", DeleteRequest(
            operation_id=uuid4(), expected_revision=2,
        ))
        await repository.delete(source.dataset_id, "dataset", DeleteRequest(
            operation_id=uuid4(), expected_revision=2,
        ))


async def test_dashboard_dataset_registry_protects_unpublished_draft(viz):
    source = dataset()
    board = dashboard().model_copy(update={
        "datasets": (DatasetReference(dataset_id=source.dataset_id, revision=1),),
    })
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
        await repository.create(viz.workspace_id, board, operation_id=uuid4())
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_publications") == [{"n": 0}]
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (repository, _):
            await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                operation_id=uuid4(), expected_revision=1,
            ))
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 2}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_revision_dependencies") == [
        {"n": 1},
    ]


async def test_dashboard_dataset_registry_refuses_hidden_source_for_workspace_editor(viz):
    source = dataset()
    board = dashboard().model_copy(update={
        "datasets": (DatasetReference(dataset_id=source.dataset_id, revision=1),),
    })
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
    with pytest.raises(NovaException) as refused:
        async with viz.transaction(session("editor", active="EDITOR")) as (repository, _):
            await repository.create(viz.workspace_id, board, operation_id=uuid4())
    assert refused.value.status_code == 404
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources "
                             "WHERE resource_id=%s", (board.dashboard_id,)) == [{"n": 0}]


async def test_dashboard_dataset_registry_refuses_cross_workspace_reference(viz):
    source = dataset()
    async with viz.transaction(repository_type=WorkspaceSharingRepository) as (spaces, _):
        other = await spaces.create("Separate data workspace")
    async with viz.transaction() as (repository, _):
        await repository.create(other["id"], source, operation_id=uuid4())
    board = dashboard().model_copy(update={
        "datasets": (DatasetReference(dataset_id=source.dataset_id, revision=1),),
    })
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction() as (repository, _):
            await repository.create(viz.workspace_id, board, operation_id=uuid4())


@pytest_asyncio.fixture
async def native_service(viz, monkeypatch):
    database = MetadataDatabase(viz.environment.role_config("nova_meta_web"))
    await database.open(schema_version=len(load_migrations()), metadata_epoch=viz.epoch)
    records = {"login-owner": session("owner"),
               "login-reader": session("reader", active="VIEWER")}

    async def get_login(identity):
        return records.get(identity)

    monkeypatch.setattr(metadata_scope, "metadata_db", lambda: database)
    monkeypatch.setattr(document_service.session_store, "get", get_login)
    try:
        yield NativeDocumentService(), records
    finally:
        metadata_scope.clear_actor()
        await database.close()


@pytest_asyncio.fixture
async def dataset_author(viz, native_service, monkeypatch):
    service, records = native_service
    source = dataset()
    request = ObjectDatasetCreate(
        dataset_id=source.dataset_id, title=source.title, workspace_id=viz.workspace_id,
        source={key: value for key, value in source.source.model_dump(exclude_none=True).items()
                if key != "physical_id"},
        expected_physical_id=source.source.physical_id,
        expected_schema_fingerprint=source.schema_fingerprint,
    )
    author = NativeDatasetService()

    async def definition(_user, intent):
        assert metadata_scope.active_scope() is None
        return source.model_copy(update={"title": intent.title})

    inspect = AsyncMock(side_effect=definition)
    monkeypatch.setattr(author, "_definition", inspect)
    return author, service, records, source, request, inspect


@pytest.mark.parametrize("edit", [False, True])
async def test_dataset_intent_replay_survives_source_loss_and_later_edits(
    dataset_author, viz, edit,
):
    author, service, records, source, create, inspect = dataset_author
    user = records["login-owner"]
    create_key, edit_key = uuid4(), uuid4()
    first = await author.create(user, create, operation_id=create_key)
    update = ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"}))
    second = await author.update(user, update, operation_id=edit_key, expected_revision=1)
    inspect.reset_mock()
    inspect.side_effect = NovaException("Source access refused", status_code=403)
    replay = (
        await author.update(user, update, operation_id=edit_key, expected_revision=1) if edit
        else await author.create(user, create, operation_id=create_key)
    )
    assert replay == (second if edit else first)
    inspect.assert_not_awaited()
    assert len(await service.history(user, source.dataset_id)) == 2
    assert len((await service.events(user, source.dataset_id))["events"]) == 2
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 2}]


@pytest.mark.parametrize("change", ["title", "source", "fingerprint", "revision", "command"])
async def test_dataset_intent_reuse_refuses_changed_command_before_source_io(
    dataset_author, change,
):
    author, _, records, _, create, inspect = dataset_author
    user = records["login-owner"]
    key = uuid4()
    await author.create(user, create, operation_id=uuid4())
    update = ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"}))
    await author.update(user, update, operation_id=key, expected_revision=1)
    inspect.reset_mock()
    revision = 1
    if change == "title":
        update = update.model_copy(update={"title": "Another title"})
    elif change == "source":
        update = update.model_copy(update={
            "source": update.source.model_copy(update={"object_name": "other"}),
        })
    elif change == "fingerprint":
        update = update.model_copy(update={"expected_schema_fingerprint": "b" * 64})
    elif change == "revision":
        revision = 2
    with pytest.raises(MetadataConflict):
        if change == "command":
            await author.create(user, create, operation_id=key)
        else:
            await author.update(user, update, operation_id=key, expected_revision=revision)
    inspect.assert_not_awaited()


@pytest.mark.parametrize("identity_field,new_value", [
    ("active_role", "EDITOR"), ("session_id", "other-login"), ("security_context_version", 6),
])
async def test_dataset_intent_replay_remains_bound_to_login_context(
    dataset_author, identity_field, new_value,
):
    author, _, records, _, create, inspect = dataset_author
    user = records["login-owner"]
    key = uuid4()
    await author.create(user, create, operation_id=key)
    changed = {**user, identity_field: new_value}
    if identity_field == "active_role":
        changed["assigned_roles"] = [new_value]
    records[changed["session_id"]] = changed
    inspect.reset_mock()
    with pytest.raises(MetadataConflict):
        await author.create(changed, create, operation_id=key)
    inspect.assert_not_awaited()


async def test_dataset_legacy_receipt_keeps_definition_validation(dataset_author, viz):
    author, _, records, source, create, inspect = dataset_author
    key = uuid4()
    async with viz.transaction() as (repository, _):
        receipt = await repository.create(viz.workspace_id, source, operation_id=key)
    assert await author.create(records["login-owner"], create, operation_id=key) == receipt
    inspect.assert_awaited_once()
    with pytest.raises(MetadataConflict):
        await author.create(records["login-owner"], create.model_copy(update={"title": "Changed"}),
                            operation_id=key)


@pytest.mark.parametrize("retain_view", [False, True])
async def test_dataset_intent_replay_refuses_revoked_editor(dataset_author, viz, retain_view):
    author, _, records, source, create, inspect = dataset_author
    await author.create(records["login-owner"], create, operation_id=uuid4())
    await grant(viz, source.dataset_id, {
        "subject_type": "user", "subject_id": "reader", "permissions": ["edit"],
    })
    update = ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"}))
    key = uuid4()
    await author.update(records["login-reader"], update, operation_id=key, expected_revision=1)
    async with viz.transaction() as (repository, _):
        await repository.replace_grants(source.dataset_id, GrantsReplace.model_validate({
            "operation_id": uuid4(), "expected_revision": 2,
            "grants": [{"subject_type": "user", "subject_id": "reader", "permissions": ["view"]}]
            if retain_view else [],
        }))
    inspect.reset_mock()
    with pytest.raises(NovaException) as refused:
        await author.update(records["login-reader"], update, operation_id=key, expected_revision=1)
    assert refused.value.status_code in {403, 404}
    inspect.assert_not_awaited()


@pytest.mark.parametrize("edit", [False, True])
async def test_dataset_source_error_resolves_concurrent_commit(dataset_author, viz, edit):
    author, _, records, source, create, inspect = dataset_author
    user = records["login-owner"]
    key = uuid4()
    update = ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"}))
    if edit:
        await author.create(user, create, operation_id=uuid4())

    async def committed_then_source_changed(_user, intent):
        assert metadata_scope.active_scope() is None
        async with viz.transaction() as (repository, _):
            if edit:
                await repository.replace(source, operation_id=key, expected_revision=1,
                                         intent=intent)
            else:
                await repository.create(viz.workspace_id, source, operation_id=key, intent=intent)
        raise NovaException("SCHEMA_STALE", status_code=409)

    inspect.side_effect = committed_then_source_changed
    receipt = (
        await author.update(user, update, operation_id=key, expected_revision=1) if edit
        else await author.create(user, create, operation_id=key)
    )
    assert receipt["operation_id"] == key and receipt["new_revision"] == (2 if edit else 1)


async def test_dashboard_dataset_creation_is_atomic_and_replayable(dataset_author, viz):
    author, service, records, source, create, inspect = dataset_author
    user, board, key = records["login-owner"], dashboard(), uuid4()
    await service.create_visual(user, viz.workspace_id, board, operation_id=uuid4())
    request = DashboardDatasetCreate(
        dataset=ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"})),
    )
    receipt = await author.create_in_dashboard(user, board.dashboard_id, request,
                                              operation_id=key, expected_revision=1)
    assert receipt["resource_id"] == board.dashboard_id and receipt["new_revision"] == 2
    saved = await service.read(user, board.dashboard_id, draft=True)
    assert saved["definition"].datasets == (
        DatasetReference(dataset_id=source.dataset_id, revision=1),
    )
    assert (await service.read(user, source.dataset_id, draft=True))["revision"] == 1
    inspect.reset_mock()
    inspect.side_effect = NovaException("Source no longer available", status_code=403)
    assert await author.create_in_dashboard(user, board.dashboard_id, request,
                                           operation_id=key, expected_revision=1) == receipt
    changed = request.model_copy(update={
        "dataset": request.dataset.model_copy(update={"title": "Another intent"}),
    })
    with pytest.raises(MetadataConflict):
        await author.create_in_dashboard(user, board.dashboard_id, changed,
                                         operation_id=key, expected_revision=1)
    inspect.assert_not_awaited()
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 3}]


async def test_dashboard_dataset_creation_rolls_back_child_on_parent_failure(
    dataset_author, viz, monkeypatch,
):
    author, service, records, source, create, _ = dataset_author
    user, board = records["login-owner"], dashboard()
    await service.create_visual(user, viz.workspace_id, board, operation_id=uuid4())
    request = DashboardDatasetCreate(
        dataset=ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"})),
    )

    async def failed_parent(repository, *_args, **_kwargs):
        child = await repository.read(source.dataset_id, draft=True)
        assert child["revision"] == 1
        raise NovaException("Injected parent failure", status_code=503)

    monkeypatch.setattr(VizDocumentRepository, "_replace", failed_parent)
    with pytest.raises(NovaException, match="Injected parent failure"):
        await author.create_in_dashboard(user, board.dashboard_id, request,
                                         operation_id=uuid4(), expected_revision=1)
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 1}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 1}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_outbox") == [{"n": 1}]
    assert (await service.read(user, board.dashboard_id, draft=True))["revision"] == 1


async def test_dashboard_dataset_creation_requires_workspace_creation_permission(
    dataset_author, viz,
):
    author, service, records, _, create, inspect = dataset_author
    board = dashboard()
    await service.create_visual(
        records["login-owner"], viz.workspace_id, board, operation_id=uuid4(),
    )
    await grant(viz, board.dashboard_id, {
        "subject_type": "user", "subject_id": "reader", "permissions": ["edit"],
    })
    request = DashboardDatasetCreate(
        dataset=ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"})),
    )
    with pytest.raises(MetadataPermissionDenied):
        await author.create_in_dashboard(records["login-reader"], board.dashboard_id, request,
                                         operation_id=uuid4(), expected_revision=1)
    inspect.assert_not_awaited()
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 1}]


async def test_dashboard_dataset_creation_concurrent_revision_has_no_orphan(dataset_author, viz):
    author, service, records, source, create, inspect = dataset_author
    user, board = records["login-owner"], dashboard()
    await service.create_visual(user, viz.workspace_id, board, operation_id=uuid4())
    reached = 0
    both = asyncio.Event()

    async def inspect_both(_user, intent):
        nonlocal reached
        assert metadata_scope.active_scope() is None
        reached += 1
        if reached == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=5)
        return source.model_copy(update={"dataset_id": intent.dataset_id})

    inspect.side_effect = inspect_both
    first = ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"}))
    intents = [first, first.model_copy(update={"dataset_id": uuid4()})]
    outcomes = await asyncio.gather(*[
        author.create_in_dashboard(user, board.dashboard_id, DashboardDatasetCreate(dataset=intent),
                                   operation_id=uuid4(), expected_revision=1)
        for intent in intents
    ], return_exceptions=True)
    assert sum(isinstance(outcome, MetadataConflict) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 2}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 3}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_outbox") == [{"n": 3}]


async def test_dashboard_dataset_creation_recovers_commit_during_source_failure(
    dataset_author, viz,
):
    author, service, records, source, create, inspect = dataset_author
    user, board, key = records["login-owner"], dashboard(), uuid4()
    await service.create_visual(user, viz.workspace_id, board, operation_id=uuid4())
    request = DashboardDatasetCreate(
        dataset=ObjectDatasetUpdate(**create.model_dump(exclude={"workspace_id"})),
    )

    async def committed(_user, _request):
        assert metadata_scope.active_scope() is None
        async with viz.transaction() as (repository, _):
            await repository.create_dashboard_dataset(board.dashboard_id, request, source,
                                                      operation_id=key, expected_revision=1)
        raise NovaException("SCHEMA_STALE", status_code=409)

    inspect.side_effect = committed
    receipt = await author.create_in_dashboard(user, board.dashboard_id, request,
                                              operation_id=key, expected_revision=1)
    assert receipt["operation_id"] == key and receipt["new_revision"] == 2
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 3}]


@pytest_asyncio.fixture
async def native_http(native_service, monkeypatch):
    service, records = native_service
    monkeypatch.setattr(settings, "SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setattr(settings, "FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(security, "_fernet", None)
    monkeypatch.setattr(document_service.session_store, "refresh", AsyncMock())
    for record in records.values():
        record["encrypted_password"] = security.encrypt_password(secrets.token_urlsafe(24))
        record["roles"] = record["assigned_roles"]
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(viz_router.router, prefix="/api/v1/viz")
    monkeypatch.setattr(viz_router, "service", service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://native.test") as client:
        yield client, records


def http_headers(user, *, key=None, revision=None):
    headers = {"Authorization": "Bearer " + security.create_access_token(
        user["username"], user["session_id"],
    )}
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    if revision is not None:
        headers["If-Match"] = f'"{revision}"'
    return headers


async def http_create(client, owner, document, workspace="personal"):
    return await client.post("/api/v1/viz/dashboards", headers=http_headers(owner, key=uuid4()),
                             json={"workspace_id": workspace,
                                   "definition": document.model_dump(mode="json")})


async def test_http_dataset_registry_operation_atomicity_and_replay(native_http, viz):
    client, records = native_http
    owner = records["login-owner"]
    source, board = dataset(), dashboard()
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
    assert (await http_create(client, owner, board, viz.workspace_id)).status_code == 201
    path = f"/api/v1/viz/dashboards/{board.dashboard_id}"
    key = uuid4()
    operation = {"operation_id": str(key), "expected_revision": 1, "changes": [
        {"op": "datasets.replace", "datasets": [
            {"dataset_id": str(source.dataset_id), "revision": 1},
        ]},
    ]}
    response = await client.post(path + "/operations", json=operation,
                                 headers=http_headers(owner, key=key, revision=1))
    assert response.status_code == 200 and response.json()["new_revision"] == 2
    replay = await client.post(path + "/operations", json=operation,
                               headers=http_headers(owner, key=key, revision=1))
    assert replay.json() == response.json()
    key = uuid4()
    response = await client.post(
        path + "/operations", headers=http_headers(owner, key=key, revision=2),
        json={"operation_id": str(key), "expected_revision": 2, "changes": [
            {"op": "dashboard.rename", "title": "Must roll back"},
            {"op": "datasets.replace", "datasets": [
                {"dataset_id": str(uuid4()), "revision": 1},
            ]},
        ]},
    )
    assert response.status_code == 404
    snapshot = (await client.get(path + "?draft=true", headers=http_headers(owner))).json()
    assert snapshot["revision"] == 2 and snapshot["definition"]["title"] == board.title
    assert snapshot["definition"]["datasets"] == [
        {"dataset_id": str(source.dataset_id), "revision": 1},
    ]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 3}]


async def test_http_document_create_publish_history_and_receipt(native_http):
    client, records = native_http
    owner = records["login-owner"]
    document = dashboard()
    key = uuid4()
    headers = http_headers(owner, key=key)
    payload = {"workspace_id": "personal", "definition": document.model_dump(mode="json")}
    created = await client.post("/api/v1/viz/dashboards", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    replay = await client.post("/api/v1/viz/dashboards", headers=headers, json=payload)
    assert replay.json() == created.json()
    path = f"/api/v1/viz/dashboards/{document.dashboard_id}"
    draft = await client.get(path + "?draft=true", headers=headers)
    assert draft.status_code == 200
    assert draft.headers["ETag"] == '"1"'
    assert draft.json()["definition"] == document.model_dump(mode="json")
    unpublished = await client.get(path, headers=headers)
    assert unpublished.status_code == 404
    key = uuid4()
    published = await client.post(
        path + "/publish", headers=http_headers(owner, key=key, revision=1),
        json={"operation_id": str(key), "expected_revision": 1},
    )
    assert published.status_code == 200, published.text
    snapshot = await client.get(path, headers=headers)
    assert snapshot.json()["metadata_revision"] == 2
    assert snapshot.headers["Cache-Control"] == "no-store"
    receipt = await client.get(path + f"/operations/{key}", headers=headers)
    assert receipt.json() == published.json()
    history = await client.get(path + "/revisions", headers=headers)
    assert [item["revision"] for item in history.json()] == [1]
    events = await client.get(path + "/events", headers=headers)
    assert [event["event_kind"] for event in events.json()["events"]] == ["create", "publish"]
    grants = await client.get(path + "/grants", headers=headers)
    assert grants.status_code == 200, grants.text
    assert grants.json() == {"revision": 1, "grants": []}
    assert grants.headers["ETag"] == '"1"'


async def test_http_concurrent_cas_is_atomic(native_http, viz):
    client, records = native_http
    owner = records["login-owner"]
    document = dashboard()
    assert (await http_create(client, owner, document)).status_code == 201
    path = f"/api/v1/viz/dashboards/{document.dashboard_id}/draft"

    async def replace(title):
        definition = document.model_copy(update={"title": title})
        return await client.patch(path, headers=http_headers(owner, key=uuid4(), revision=1),
                                   json=definition.model_dump(mode="json"))

    responses = await asyncio.gather(replace("First editor"), replace("Second editor"))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert await viz.inspect("SELECT draft_revision FROM nova_meta.viz_resources") == [
        {"draft_revision": 2},
    ]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 2}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_outbox") == [{"n": 2}]


async def test_http_permission_and_kind_boundaries(native_http, native_service, viz):
    client, records = native_http
    service, _ = native_service
    owner, reader = records["login-owner"], records["login-reader"]
    document = dashboard()
    assert (await http_create(client, owner, document, viz.workspace_id)).status_code == 201
    path = f"/api/v1/viz/dashboards/{document.dashboard_id}"
    hidden = await client.get(path + "?draft=true", headers=http_headers(reader))
    assert hidden.status_code == 404
    wrong_kind = await client.get(f"/api/v1/viz/charts/{document.dashboard_id}?draft=true",
                                   headers=http_headers(owner))
    assert wrong_kind.status_code == 404
    async with viz.transaction() as (repository, _):
        await repository.replace_grants(document.dashboard_id, GrantsReplace.model_validate({
            "operation_id": uuid4(), "expected_revision": 1, "grants": [
                {"subject_type": "user", "subject_id": "reader", "permissions": ["view"]},
            ],
        }))
    await service.publish(owner, document.dashboard_id, PublicationRequest(
        operation_id=uuid4(), expected_revision=1,
    ))
    viewed = await client.get(path, headers=http_headers(reader))
    assert viewed.status_code == 200
    for suffix in ("?draft=true", "/revisions", "/grants"):
        assert (await client.get(path + suffix, headers=http_headers(reader))).status_code == 403
    key = uuid4()
    refused = await client.post(
        path + "/publish", headers=http_headers(reader, key=key, revision=1),
        json={"operation_id": str(key), "expected_revision": 1},
    )
    assert refused.status_code == 403
    unavailable = await client.get(path + f"/operations/{key}", headers=http_headers(reader))
    assert unavailable.status_code == 404


async def test_http_headers_must_agree_and_expired_login_is_rejected(native_http, viz):
    client, records = native_http
    owner = records["login-owner"]
    document = dashboard()
    assert (await http_create(client, owner, document)).status_code == 201
    path = f"/api/v1/viz/dashboards/{document.dashboard_id}"
    wrong = await client.post(
        path + "/publish", headers=http_headers(owner, key=uuid4(), revision=1),
        json={"operation_id": str(uuid4()), "expected_revision": 1},
    )
    assert wrong.status_code == 422
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_publications") == [{"n": 0}]
    headers = http_headers(owner)
    del records["login-owner"]
    expired = await client.get(path + "?draft=true", headers=headers)
    assert expired.status_code == 401
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 1}]


async def test_http_delete_keeps_bound_receipt_and_hides_document(native_http, viz):
    client, records = native_http
    owner = records["login-owner"]
    document = dashboard()
    assert (await http_create(client, owner, document)).status_code == 201
    path = f"/api/v1/viz/dashboards/{document.dashboard_id}"
    key = uuid4()
    headers = http_headers(owner, key=key, revision=1)
    body = {"operation_id": str(key), "expected_revision": 1}
    deleted = await client.request("DELETE", path, headers=headers, json=body)
    assert deleted.status_code == 200, deleted.text
    replay = await client.request("DELETE", path, headers=headers, json=body)
    assert replay.json() == deleted.json()
    receipt = await client.get(f"/api/v1/viz/operations/{key}", headers=headers)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json() == deleted.json()
    assert (await client.get(path + "?draft=true", headers=headers)).status_code == 404
    listing = await client.get("/api/v1/viz/dashboards?workspace_id=personal&draft=true",
                               headers=headers)
    assert listing.json() == []
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_revisions") == [{"n": 1}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 2}]
    owner["security_context_version"] += 1
    assert (await client.get(f"/api/v1/viz/operations/{key}", headers=headers)).status_code == 404


async def test_owner_deletion_rejects_stale_revision_and_manager_direct_sql(viz):
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    await grant(viz, identity, {"subject_type": "role", "subject_id": "EDITOR",
                                "permissions": ["manage"]})
    with pytest.raises(MetadataConflict):
        async with viz.transaction() as (repository, _):
            await repository.delete(identity, "dashboard", DeleteRequest(
                operation_id=uuid4(), expected_revision=2,
            ))
    manager = session("manager", active="EDITOR")
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(manager) as (repository, _):
            await repository.delete(identity, "dashboard", DeleteRequest(
                operation_id=uuid4(), expected_revision=1,
            ))
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(manager) as (_, connection):
            await connection.execute(
                "UPDATE nova_meta.viz_resources SET deleted_at=clock_timestamp(),"
                "metadata_revision=metadata_revision+1 WHERE resource_id=%s", (identity,),
            )
    assert await viz.inspect("SELECT deleted_at FROM nova_meta.viz_resources") == [
        {"deleted_at": None},
    ]


async def test_dependency_delete_is_blocked_without_dropping_shared_charts(viz):
    source, figure, board = await graph(viz)
    second = board.model_copy(update={"dashboard_id": uuid4(), "title": "Second dashboard"})
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, second, operation_id=uuid4())
    for identity, kind in ((source.dataset_id, "dataset"), (figure.chart_id, "chart")):
        with pytest.raises(MetadataIntegrityViolation):
            async with viz.transaction() as (repository, _):
                await repository.delete(identity, kind, DeleteRequest(
                    operation_id=uuid4(), expected_revision=1,
                ))
    async with viz.transaction() as (repository, _):
        await repository.delete(board.dashboard_id, "dashboard", DeleteRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (repository, _):
            await repository.delete(figure.chart_id, "chart", DeleteRequest(
                operation_id=uuid4(), expected_revision=1,
            ))
    async with viz.transaction() as (repository, _):
        await repository.delete(second.dashboard_id, "dashboard", DeleteRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    # Removing the parent releases its pins, without mutating either child.
    async with viz.transaction() as (repository, _):
        assert (await repository.read(figure.chart_id, draft=True))["revision"] == 1
        await repository.delete(figure.chart_id, "chart", DeleteRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    async with viz.transaction() as (repository, _):
        await repository.delete(source.dataset_id, "dataset", DeleteRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_revisions") == [{"n": 4}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 8}]


async def test_transitive_draft_pin_protects_old_chart_dataset_revision(viz):
    source, figure, _ = await graph(viz)
    replacement = dataset()
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, replacement, operation_id=uuid4())
        await repository.replace(figure.model_copy(update={"dataset_id": replacement.dataset_id}),
                                  operation_id=uuid4(), expected_revision=1)
    # The dashboard still pins chart revision 1, even though the chart draft
    # now reads a different dataset. Its transitive source must remain live.
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (repository, _):
            await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                operation_id=uuid4(), expected_revision=1,
            ))


async def test_retained_publication_protects_dependencies_after_draft_removal(viz):
    _, figure, board = await graph(viz)
    async with viz.transaction() as (repository, _):
        await repository.publish(board.dashboard_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    async with viz.transaction() as (repository, _):
        empty = board.model_copy(update={"pages": (Page(page_id=uuid4(), title="Empty"),)})
        await repository.replace(empty, operation_id=uuid4(), expected_revision=1)
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (repository, _):
            await repository.delete(figure.chart_id, "chart", DeleteRequest(
                operation_id=uuid4(), expected_revision=1,
            ))


async def test_reference_commit_wins_against_concurrent_delete(viz):
    source = dataset()
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
    entered = asyncio.Event()

    async def delete():
        try:
            async with viz.transaction() as (repository, _):
                entered.set()
                await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                    operation_id=uuid4(), expected_revision=1,
                ))
        except MetadataIntegrityViolation:
            return "dependency_conflict"

    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, chart(source, dataset_revision=1),
                                 operation_id=uuid4())
        pending = asyncio.create_task(delete())
        await entered.wait()
        assert not pending.done()
    assert await pending == "dependency_conflict"
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources "
                             "WHERE deleted_at IS NULL") == [{"n": 2}]


async def test_delete_commit_prevents_concurrent_new_reference(viz):
    source = dataset()
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
    entered = asyncio.Event()

    async def reference():
        try:
            async with viz.transaction() as (repository, _):
                entered.set()
                await repository.create(viz.workspace_id, chart(source, dataset_revision=1),
                                         operation_id=uuid4())
        except NovaException as error:
            return error.status_code

    async with viz.transaction() as (repository, _):
        await repository.delete(source.dataset_id, "dataset", DeleteRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
        pending = asyncio.create_task(reference())
        await entered.wait()
    assert await pending in {403, 404}
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources "
                             "WHERE deleted_at IS NULL") == [{"n": 0}]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations") == [{"n": 2}]


async def test_dependency_edges_cannot_be_removed_by_application_login(viz):
    await graph(viz)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction() as (_, connection):
            await connection.execute("DELETE FROM nova_meta.viz_revision_dependencies")
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_revision_dependencies") == [
        {"n": 2},
    ]


async def test_dependency_upgrade_backfills_exact_revision_pins(
    environment, tmp_path, monkeypatch,  # noqa: F811
):
    await environment.bootstrap()
    previous = tuple(m for m in load_migrations() if m.version <= 49)
    for migration in previous:
        (tmp_path / migration.name).write_text(migration.statement)
    await migrate(environment.role_config("nova_meta_migrator"), app_version="a" * 40,
                  directory=tmp_path)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    fixture = VizFixture(environment, epoch, "personal", schema_version=49)
    with monkeypatch.context() as old_application:
        old_application.setattr(metadata_db_module, "load_migrations", lambda: previous)
        source, figure, board = await graph(fixture)
    snapshot_sql = ("SELECT resource_id,revision,digest FROM nova_meta.viz_revisions "
                    "ORDER BY resource_id,revision")
    before = await fixture.inspect(snapshot_sql)
    await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    await migrate(environment.role_config("nova_meta_migrator"), app_version="b" * 40)
    await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=false")
    fixture.schema_version = len(load_migrations())
    after = await fixture.inspect(snapshot_sql)
    assert after == before
    edges = await fixture.inspect("SELECT resource_id,revision,depends_on,depends_revision "
                                   "FROM nova_meta.viz_revision_dependencies")
    assert {(e["resource_id"], e["revision"], e["depends_on"], e["depends_revision"])
            for e in edges} == {
        (board.dashboard_id, 1, figure.chart_id, 1), (figure.chart_id, 1, source.dataset_id, 1),
    }
    with pytest.raises(MetadataIntegrityViolation):
        async with fixture.transaction() as (repository, _):
            await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                operation_id=uuid4(), expected_revision=1,
            ))


@pytest.mark.parametrize("deleted_source", [False, True])
async def test_dashboard_dataset_dependency_upgrade_from_51_preserves_history(
    environment, tmp_path, monkeypatch, deleted_source,  # noqa: F811
):
    await environment.bootstrap()
    previous = tuple(m for m in load_migrations() if m.version <= 51)
    for migration in previous:
        (tmp_path / migration.name).write_text(migration.statement)
    await migrate(environment.role_config("nova_meta_migrator"), app_version="a" * 40,
                  directory=tmp_path)
    epoch = str(uuid4())
    await environment.admin.execute(
        "UPDATE nova_meta.deployment_state SET metadata_epoch=%s,maintenance=false", (epoch,),
    )
    fixture = VizFixture(environment, epoch, "personal", schema_version=51)
    source = dataset()
    board = dashboard().model_copy(update={
        "datasets": (DatasetReference(dataset_id=source.dataset_id, revision=1),),
    })
    with monkeypatch.context() as old_application:
        old_application.setattr(metadata_db_module, "load_migrations", lambda: previous)
        async with fixture.transaction() as (repository, _):
            await repository.create("personal", source, operation_id=uuid4())
            await repository.create("personal", board, operation_id=uuid4())
            if deleted_source:
                await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                    operation_id=uuid4(), expected_revision=1,
                ))
    assert await fixture.inspect(
        "SELECT count(*) AS n FROM nova_meta.viz_revision_dependencies",
    ) == [{"n": 0}]
    snapshot_sql = ("SELECT resource_id,revision,digest FROM nova_meta.viz_revisions "
                    "ORDER BY resource_id,revision")
    before = await fixture.inspect(snapshot_sql)
    await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=true")
    if deleted_source:
        with pytest.raises(MetadataOperationFailed):
            await migrate(environment.role_config("nova_meta_migrator"), app_version="b" * 40)
        assert await fixture.inspect(
            "SELECT max(version) AS version FROM nova_meta.schema_migrations",
        ) == [{"version": 51}]
        assert await fixture.inspect(
            "SELECT count(*) AS n FROM nova_meta.viz_revision_dependencies",
        ) == [{"n": 0}]
    else:
        await migrate(environment.role_config("nova_meta_migrator"), app_version="b" * 40)
        await environment.admin.execute("UPDATE nova_meta.deployment_state SET maintenance=false")
        fixture.schema_version = len(load_migrations())
        assert await fixture.inspect(
            "SELECT resource_id,revision,depends_on,depends_revision "
            "FROM nova_meta.viz_revision_dependencies",
        ) == [{"resource_id": board.dashboard_id, "revision": 1,
               "depends_on": source.dataset_id, "depends_revision": 1}]
        with pytest.raises(MetadataIntegrityViolation):
            async with fixture.transaction() as (repository, _):
                await repository.delete(source.dataset_id, "dataset", DeleteRequest(
                    operation_id=uuid4(), expected_revision=1,
                ))
    assert await fixture.inspect(snapshot_sql) == before


async def test_service_commits_personal_visual_history_and_replay(native_service):
    service, records = native_service
    user = records["login-owner"]
    document = dashboard()
    operation = uuid4()
    created = await service.create_visual(user, "personal", document, operation_id=operation)
    assert await service.receipt(user, document.dashboard_id, operation) == created
    assert (await service.browse(user, "personal", draft=True))[0]["title"] == "Sales"
    edited = document.model_copy(update={"title": "Updated sales"})
    await service.replace_visual(user, edited, operation_id=uuid4(), expected_revision=1)
    await service.publish(user, document.dashboard_id, PublicationRequest(
        operation_id=uuid4(), expected_revision=2,
    ))
    assert (await service.read(user, document.dashboard_id))["definition"] == edited
    assert len(await service.history(user, document.dashboard_id)) == 2
    assert len((await service.events(user, document.dashboard_id))["events"]) == 3
    assert "manage" in await service.permissions(user, document.dashboard_id)
    assert await service.browse(records["login-reader"], "personal") == []


async def test_service_directory_validation_and_atomic_refusal_audit(
    viz, native_service, monkeypatch,
):
    service, records = native_service
    user, reader = records["login-owner"], records["login-reader"]
    document = dashboard()
    await service.create_visual(user, viz.workspace_id, document, operation_id=uuid4())
    await service.publish(user, document.dashboard_id, PublicationRequest(
        operation_id=uuid4(), expected_revision=1,
    ))

    async def users():
        assert metadata_scope.active_scope() is None
        return [{"username": "reader"}]

    monkeypatch.setattr(document_service.user_service, "list_users", users)
    request = GrantsReplace.model_validate({
        "operation_id": uuid4(), "expected_revision": 1, "grants": [
            {"subject_type": "user", "subject_id": "reader", "permissions": ["view"]},
        ],
    })
    await service.replace_grants(user, document.dashboard_id, request)
    assert (await service.read(reader, document.dashboard_id))["definition"] == document
    with pytest.raises(MetadataPermissionDenied):
        await service.grants(reader, document.dashboard_id)
    rows = await viz.inspect(
        "SELECT user_name,active_role,object_name,sql_text,decision FROM nova_meta.audit_log "
        "WHERE event_type='viz' AND status='DENIED'"
    )
    assert rows == [{"user_name": "reader", "active_role": "VIEWER",
                     "object_name": str(document.dashboard_id), "sql_text": None,
                     "decision": "DENY"}]


async def test_service_rejects_stale_session_without_committing_any_document(viz, native_service):
    service, records = native_service
    stale = dict(records["login-owner"])
    records["login-owner"] = {**stale, "security_context_version": 6}
    with pytest.raises(MetadataPermissionDenied):
        await service.read(stale, uuid4())
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 0}]
    assert await viz.inspect(
        "SELECT count(*) AS n FROM nova_meta.audit_log WHERE event_type='viz' AND decision='DENY'"
    ) == [{"n": 1}]


@pytest.mark.parametrize("action", ["LIST", "CREATE"])
async def test_service_workspace_admission_denials_commit_a_redacted_audit(
    viz, native_service, action,
):
    service, records = native_service
    reader = records["login-reader"]
    document = dashboard("private-title-canary")
    with pytest.raises(NovaException) as error:
        if action == "CREATE":
            await service.create_visual(reader, viz.workspace_id, document, operation_id=uuid4())
        else:
            await service.browse(reader, str(uuid4()))
    assert error.value.status_code == (403 if action == "CREATE" else 404)
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 0}]
    rows = await viz.inspect(
        "SELECT user_name,active_role,action,object_name,sql_text,error_message,decision "
        "FROM nova_meta.audit_log WHERE event_type='viz' AND status='DENIED'"
    )
    assert rows == [{"user_name": "reader", "active_role": "VIEWER", "action": action,
                     "object_name": str(document.dashboard_id) if action == "CREATE"
                     else "document_collection", "sql_text": None, "error_message": None,
                     "decision": "DENY"}]


async def test_http_revoked_assignment_cannot_reuse_legacy_session_roles(native_http, viz):
    client, records = native_http
    owner = records["login-owner"]
    document = dashboard()
    headers = http_headers(owner, key=uuid4())
    owner["assigned_roles"] = []
    owner["roles"] = ["ANALYST"]
    response = await client.post("/api/v1/viz/dashboards", headers=headers, json={
        "workspace_id": "personal", "definition": document.model_dump(mode="json"),
    })
    assert response.status_code == 403
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 0}]


async def create(viz, document=None):
    document = document or dashboard()
    operation_id = uuid4()
    async with viz.transaction() as (repo, _):
        receipt = await repo.create(viz.workspace_id, document, operation_id=operation_id)
    return document, operation_id, receipt


async def test_personal_documents_use_existing_private_workspace_namespace(viz):
    document = dashboard()
    async with viz.transaction() as (repository, _):
        await repository.create("personal", document, operation_id=uuid4())
        await repository.publish(document.dashboard_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
        assert (await repository.browse("personal"))[0]["resource_id"] == document.dashboard_id
    row = (await viz.inspect(
        "SELECT workspace_namespace,shared_workspace_id FROM nova_meta.viz_resources"
    ))[0]
    assert row == {"workspace_namespace": "owner", "shared_workspace_id": None}
    # A document grant never creates membership in another user's personal space.
    await grant(viz, document.dashboard_id, {
        "subject_type": "user", "subject_id": "reader", "permissions": ["manage", "export"],
    })
    async with viz.transaction(session("reader", active="VIEWER")) as (repository, _):
        assert await repository.browse("personal") == []
        with pytest.raises(NovaException, match="Document not found"):
            await repository.read(document.dashboard_id)
        own = dashboard("Reader's personal document")
        await repository.create("personal", own, operation_id=uuid4())
        assert [row["resource_id"] for row in await repository.browse("personal", draft=True)] == [
            own.dashboard_id,
        ]
    async with viz.transaction() as (repository, _):
        rows = await repository.browse("personal", draft=True)
        assert [row["resource_id"] for row in rows] == [document.dashboard_id]
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.config_workspaces") == [{"n": 1}]


async def test_personal_binding_cannot_borrow_another_principals_dataset(viz):
    source = dataset()
    async with viz.transaction() as (repository, _):
        await repository.create("personal", source, operation_id=uuid4())
    await grant(viz, source.dataset_id, {
        "subject_type": "user", "subject_id": "reader", "permissions": ["edit"],
    })
    with pytest.raises(NovaException, match="Document not found"):
        async with viz.transaction(session("reader", active="VIEWER")) as (repository, _):
            await repository.create("personal", chart(source, dataset_revision=1),
                                    operation_id=uuid4())
    assert await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_resources") == [{"n": 1}]


async def test_personal_namespace_cannot_be_forged_or_used_without_bound_roles(viz):
    document = dashboard()
    async with viz.transaction() as (repository, _):
        await repository.create("personal", document, operation_id=uuid4())
    with pytest.raises(MetadataOperationFailed):
        async with viz.transaction() as (_, connection):
            await connection.execute(
                "UPDATE nova_meta.viz_resources SET workspace_namespace=%s WHERE resource_id=%s",
                ("reader", document.dashboard_id),
            )
    async with viz.transaction() as (_, connection):
        await connection.execute("SELECT set_config('nova.workspace_roles','',true)")
        cursor = await connection.execute("SELECT resource_id FROM nova_meta.viz_resources")
        assert await cursor.fetchall() == []
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction() as (repository, _):
            await repository.create("nonexistent-workspace", dashboard(), operation_id=uuid4())


async def grant(viz, identity, *grants, revision=1):
    request = GrantsReplace.model_validate({"operation_id": str(uuid4()),
        "expected_revision": revision, "grants": grants})
    async with viz.transaction() as (repo, _):
        return await repo.replace_grants(identity, request)


async def test_published_dataset_http_projection_hides_sql_from_owner_and_viewer(
    viz, native_http
):
    client, records = native_http
    source = dataset()
    source = source.model_copy(update={"source": SqlSource(
        sql="SELECT amount FROM orders /* private-sql-publication-canary */",
        ast_digest="a" * 64, catalog="default_catalog", database="sales",
        dependencies=(source.source,),
    )})
    async with viz.transaction() as (repo, _):
        await repo.create(viz.workspace_id, source, operation_id=uuid4())
        await repo.publish(source.dataset_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    await grant(viz, source.dataset_id, {
        "subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"],
    })
    path = f"/api/v1/viz/datasets/{source.dataset_id}"
    for name in ("login-owner", "login-reader"):
        response = await client.get(path, headers=http_headers(records[name]))
        assert response.status_code == 200
        definition = response.json()["definition"]
        assert definition["definition_scope"] == "publication"
        assert definition["source_kind"] == "sql"
        assert definition["fields"] == source.model_dump(mode="json")["fields"]
        assert "private-sql-publication-canary" not in response.text
        assert "source" not in definition and "schema_fingerprint" not in definition
    draft = await client.get(path + "?draft=true", headers=http_headers(records["login-owner"]))
    assert draft.status_code == 200 and "private-sql-publication-canary" in draft.text
    refused = await client.get(path + "?draft=true", headers=http_headers(records["login-reader"]))
    assert refused.status_code == 403 and "private-sql-publication-canary" not in refused.text
    async with viz.transaction() as (repo, _):
        internal = await repo.read(source.dataset_id)
        assert internal["definition"] == source


async def test_historical_dataset_read_returns_exact_revision_only_to_current_editors(
    viz, native_http,
):
    client, records = native_http
    source = dataset()
    edited = source.model_copy(update={"title": "New draft title"})
    async with viz.transaction() as (repository, _):
        await repository.create(viz.workspace_id, source, operation_id=uuid4())
        await repository.publish(source.dataset_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
        await repository.replace(edited, operation_id=uuid4(), expected_revision=1)
    await grant(viz, source.dataset_id, {
        "subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"],
    }, {"subject_type": "role", "subject_id": "EDITOR", "permissions": ["edit"]})
    path = f"/api/v1/viz/datasets/{source.dataset_id}"
    owner_headers = http_headers(records["login-owner"])
    reader_headers = http_headers(records["login-reader"])
    old = await client.get(path + "?draft=true&revision=1", headers=owner_headers)
    assert old.status_code == 200 and old.headers["ETag"] == '"1"'
    assert old.json()["definition"] == source.model_dump(mode="json")
    head = await client.get(path + "?draft=true", headers=owner_headers)
    assert head.status_code == 200 and head.json()["definition"]["title"] == edited.title
    pinned_preview = await NativeDatasetService()._preview_snapshot(
        records["login-owner"], source.dataset_id, DatasetPreviewRequest(revision=1),
    )
    assert pinned_preview["revision"] == 1 and pinned_preview["definition"] == source
    denied = await client.get(path + "?draft=true&revision=1", headers=reader_headers)
    assert denied.status_code == 403 and "physical_id" not in denied.text
    for headers in (owner_headers, reader_headers):
        # Publication scope may read a revision a visible publication pins; it
        # returns the source-free projection, never the private definition.
        pinned_view = await client.get(path + "?revision=1", headers=headers)
        assert pinned_view.status_code == 200 and "physical_id" not in pinned_view.text
        assert pinned_view.json()["definition"]["definition_scope"] == "publication"
    unpublished = await client.get(path + "?revision=2", headers=reader_headers)
    assert unpublished.status_code == 404 and "physical_id" not in unpublished.text
    absent = await client.get(path + "?draft=true&revision=3", headers=owner_headers)
    assert absent.status_code == 404
    editor = session("editor", active="EDITOR", roles=["EDITOR"])
    async with viz.transaction(editor) as (repository, _):
        pinned = await repository.read(source.dataset_id, draft=True, revision=1)
        assert pinned["definition"] == source
    await grant(viz, source.dataset_id, {
        "subject_type": "role", "subject_id": "EDITOR", "permissions": ["view"],
    }, revision=2)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(editor) as (repository, _):
            await repository.read(source.dataset_id, draft=True, revision=1)


async def test_dashboard_dataset_index_uses_pinned_titles_and_current_child_permissions(
    viz, native_http,
):
    client, records = native_http
    first, second = dataset(), dataset(title="Second dataset")
    board = dashboard().model_copy(update={"datasets": (
        DatasetReference(dataset_id=second.dataset_id, revision=1),
        DatasetReference(dataset_id=first.dataset_id, revision=1),
    )})
    async with viz.transaction() as (repository, _):
        for item in (first, second, board):
            await repository.create(viz.workspace_id, item, operation_id=uuid4())
        await repository.replace(first.model_copy(update={"title": "New draft name"}),
                                 operation_id=uuid4(), expected_revision=1)
    for identity in (first.dataset_id, second.dataset_id, board.dashboard_id):
        await grant(viz, identity, {
            "subject_type": "role", "subject_id": "VIEWER", "permissions": ["edit"],
        })
    path = f"/api/v1/viz/dashboards/{board.dashboard_id}/datasets?revision=1"
    response = await client.get(path, headers=http_headers(records["login-reader"]))
    assert response.status_code == 200
    assert response.json() == {"dashboard_id": str(board.dashboard_id), "revision": 1,
                               "datasets": [
        {"dataset_id": str(second.dataset_id), "revision": 1, "title": second.title},
        {"dataset_id": str(first.dataset_id), "revision": 1, "title": first.title},
    ]}
    await grant(viz, first.dataset_id, {
        "subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"],
    }, revision=2)
    limited = await client.get(path, headers=http_headers(records["login-reader"]))
    assert limited.status_code == 200
    assert limited.json()["datasets"][1] == {
        "dataset_id": str(first.dataset_id), "revision": 1, "title": None,
    }
    assert "New draft name" not in limited.text and "physical_id" not in limited.text
    await grant(viz, board.dashboard_id, {
        "subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"],
    }, revision=2)
    denied = await client.get(path, headers=http_headers(records["login-reader"]))
    assert denied.status_code == 403 and second.title not in denied.text
    wrong_kind = await client.get(
        f"/api/v1/viz/dashboards/{first.dataset_id}/datasets?revision=1",
        headers=http_headers(records["login-owner"]),
    )
    assert wrong_kind.status_code == 404


async def test_empty_dashboard_dataset_index_has_no_fabricated_sources(viz):
    board, _, _ = await create(viz)
    async with viz.transaction() as (repository, _):
        assert await repository.dashboard_datasets(board.dashboard_id, 1) == {
            "dashboard_id": board.dashboard_id, "revision": 1, "datasets": [],
        }


async def test_create_commits_revision_receipt_outbox_and_redacted_audit_together(viz):
    document, _, receipt = await create(viz)
    identity = document.dashboard_id
    assert receipt["new_revision"] == receipt["event_sequence"] == 1
    async with viz.transaction() as (repo, _):
        assert (await repo.read(identity, draft=True))["definition"] == document
    for table in ("viz_revisions", "viz_operations", "viz_outbox"):
        assert (await viz.inspect(f"SELECT count(*) AS n FROM nova_meta.{table}"))[0]["n"] == 1
    audit = await viz.inspect("SELECT metadata_key,sql_text FROM nova_meta.audit_log "
                             "WHERE metadata_table='viz_resources'")
    assert audit[0]["metadata_key"] == {"resource_id": str(identity)}
    assert audit[0]["sql_text"] is None


async def test_ack_loss_retry_returns_exact_receipt_and_changed_payload_conflicts(viz):
    document, operation_id, first = await create(viz)
    async with viz.transaction() as (repo, _):
        assert await repo.create(viz.workspace_id, document, operation_id=operation_id) == first
    with pytest.raises(MetadataConflict):
        async with viz.transaction() as (repo, _):
            await repo.create(viz.workspace_id, document.model_copy(update={"title": "Changed"}),
                              operation_id=operation_id)
    assert (await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations"))[0]["n"] == 1


async def test_two_edits_have_one_winner_and_one_revision_conflict(viz):
    document, _, _ = await create(viz)
    barrier = asyncio.Barrier(2)

    async def edit(title):
        try:
            async with viz.transaction() as (repo, _):
                await barrier.wait()
                return await repo.replace(document.model_copy(update={"title": title}),
                                          operation_id=uuid4(), expected_revision=1)
        except MetadataConflict:
            return "conflict"

    results = await asyncio.gather(edit("First"), edit("Second"))
    assert results.count("conflict") == 1
    assert (await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_revisions"))[0]["n"] == 2
    assert (await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_outbox"))[0]["n"] == 2


async def test_role_grant_is_active_only_and_workspace_membership_is_separate(viz):
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    await grant(viz, identity, {"subject_type": "role", "subject_id": "VIEWER",
                                "permissions": ["view", "interact"]})
    async with viz.transaction() as (repo, _):
        await repo.publish(identity, PublicationRequest(operation_id=uuid4(), expected_revision=1))
    active = session("viewer", active="VIEWER", roles=["ANALYST", "VIEWER"])
    async with viz.transaction(active) as (repo, connection):
        assert (await repo.read(identity))["definition"].title == "Sales"
        cursor = await connection.execute(
            "SELECT nova_meta.viz_can(%s,'export') AS allowed", (identity,),
        )
        assert (await cursor.fetchone())["allowed"] is False
        with pytest.raises(MetadataPermissionDenied):
            await repo.read(identity, draft=True)
    inactive = session("viewer", active="ANALYST", roles=["ANALYST", "VIEWER"])
    with pytest.raises(NovaException) as error:
        async with viz.transaction(inactive) as (repo, _):
            await repo.read(identity)
    assert error.value.status_code == 404
    await grant(viz, identity, {"subject_type": "user", "subject_id": "outsider",
                                "permissions": ["view"]}, revision=2)
    with pytest.raises(NovaException):
        async with viz.transaction(session("outsider")) as (repo, _):
            await repo.read(identity)


async def test_publication_stays_immutable_then_restore_creates_new_draft(viz):
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    async with viz.transaction() as (repo, _):
        await repo.publish(identity, PublicationRequest(operation_id=uuid4(), expected_revision=1))
    async with viz.transaction() as (repo, _):
        publication_id = (await repo.read(identity))["publication_id"]
        await repo.replace(document.model_copy(update={"title": "New title"}),
                           operation_id=uuid4(), expected_revision=1)
    async with viz.transaction() as (repo, _):
        assert (await repo.read(identity))["definition"].title == "Sales"
        assert (await repo.read(identity, draft=True))["definition"].title == "New title"
        receipt = await repo.restore(identity, RestoreRequest(
            operation_id=uuid4(), expected_revision=2, publication_id=publication_id,
        ))
        assert receipt["new_revision"] == 3
    async with viz.transaction() as (repo, _):
        assert (await repo.read(identity, draft=True))["definition"].title == "Sales"
        assert (await repo.read(identity))["revision"] == 1


async def test_expired_grant_and_inactive_role_do_not_count_as_document_access(viz):
    document, _, _ = await create(viz)
    await grant(viz, document.dashboard_id, {
        "subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"],
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    })
    async with viz.transaction(session("viewer", active="VIEWER")) as (_, connection):
        cursor = await connection.execute("SELECT count(*) AS n FROM nova_meta.viz_resources")
        assert (await cursor.fetchone())["n"] == 0


async def test_direct_revision_update_and_owner_spoof_are_denied(viz):
    document, _, _ = await create(viz)
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction() as (_, connection):
            await connection.execute("UPDATE nova_meta.viz_revisions SET definition='{}'::jsonb")
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction() as (_, connection):
            await connection.execute("UPDATE nova_meta.viz_resources SET owner_name='someone-else'")
    async with viz.transaction() as (repo, _):
        assert (await repo.read(document.dashboard_id, draft=True))["definition"] == document


async def test_listing_never_exposes_unpublished_titles_to_viewers(viz):
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    await grant(viz, identity, {"subject_type": "role", "subject_id": "VIEWER",
                                "permissions": ["view", "interact"]})
    viewer = session("viewer", active="VIEWER")
    async with viz.transaction(viewer) as (repo, _):
        assert await repo.browse(viz.workspace_id) == []
        assert await repo.browse(viz.workspace_id, draft=True) == []
    async with viz.transaction() as (repo, _):
        await repo.publish(identity, PublicationRequest(operation_id=uuid4(), expected_revision=1))
        await repo.replace(document.model_copy(update={"title": "Private next draft"}),
                           operation_id=uuid4(), expected_revision=1)
    async with viz.transaction(viewer) as (repo, _):
        visible = await repo.browse(viz.workspace_id)
        assert [(row["resource_id"], row["revision"], row["title"]) for row in visible] == [
            (identity, 1, "Sales"),
        ]
        assert "definition" not in visible[0]
        assert await repo.browse(viz.workspace_id, draft=True) == []
        assert await repo.permissions(identity) == ["view", "interact"]
        with pytest.raises(MetadataPermissionDenied):
            await repo.history(identity)
        with pytest.raises(MetadataPermissionDenied):
            await repo.grants(identity)
        with pytest.raises(NovaException, match="revision not found"):
            await repo.revision(identity, 2)
        assert (await repo.revision(identity, 1)).title == "Sales"
    async with viz.transaction() as (repo, _):
        assert (await repo.browse(viz.workspace_id, draft=True))[0]["title"] == "Private next draft"
    await grant(viz, identity, revision=2)
    async with viz.transaction(viewer) as (repo, _):
        assert await repo.browse(viz.workspace_id) == []
        with pytest.raises(NovaException, match="Document not found"):
            await repo.events(identity)


async def test_history_listing_and_event_cursors_are_bounded_and_complete(viz):
    documents = [(await create(viz))[0] for _ in range(3)]
    identity = documents[0].dashboard_id
    async with viz.transaction() as (repo, _):
        await repo.replace(documents[0].model_copy(update={"title": "Second revision"}),
                           operation_id=uuid4(), expected_revision=1)
        await repo.publish(identity, PublicationRequest(operation_id=uuid4(), expected_revision=2))
    async with viz.transaction() as (repo, _):
        first = await repo.browse(viz.workspace_id, draft=True, limit=1)
        cursor = (first[0]["updated_at"], first[0]["resource_id"])
        remaining = await repo.browse(viz.workspace_id, draft=True, before=cursor, limit=2)
        assert {row["resource_id"] for row in first + remaining} == {
            document.dashboard_id for document in documents
        }
        history = await repo.history(identity, limit=1)
        assert [row["revision"] for row in history] == [2]
        assert [row["revision"] for row in await repo.history(identity, before=2)] == [1]
        assert "definition" not in history[0]
        events = await repo.events(identity, limit=2)
        assert events["through"] == 3
        assert events["next_sequence"] == 2
        assert [row["event_kind"] for row in events["events"]] == ["create", "edit"]
        tail = await repo.events(identity, after=events["next_sequence"])
        assert [row["event_kind"] for row in tail["events"]] == ["publish"]
        assert (await repo.events(identity, after=3))["events"] == []
        with pytest.raises(MetadataConflict):
            await repo.events(identity, after=4)
        for invalid in (0, 101, True, -1):
            with pytest.raises(ValueError, match="Page size"):
                await repo.browse(viz.workspace_id, limit=invalid)


async def test_receipt_lookup_is_bound_to_the_original_security_context(viz):
    document, operation_id, receipt = await create(viz)
    identity = document.dashboard_id
    async with viz.transaction() as (repo, _):
        assert await repo.receipt(identity, operation_id) == receipt
    for changes in ({"session_id": "new-login"}, {"security_context_version": 6},
                    {"active": "EDITOR", "roles": ["ANALYST", "EDITOR"]}):
        async with viz.transaction(session("owner", **changes)) as (repo, _):
            with pytest.raises(NovaException, match="receipt not found"):
                await repo.receipt(identity, operation_id)
    await grant(viz, identity, {"subject_type": "role", "subject_id": "EDITOR",
                                "permissions": ["edit"]})
    async with viz.transaction(session("other-editor", active="EDITOR")) as (repo, _):
        with pytest.raises(NovaException, match="receipt not found"):
            await repo.receipt(identity, operation_id)


async def test_manager_can_resolve_acknowledgement_after_downgrading_their_grant(viz):
    document, _, _ = await create(viz)
    identity = document.dashboard_id
    await grant(viz, identity, {"subject_type": "role", "subject_id": "EDITOR",
                                "permissions": ["manage"]})
    manager = session("manager", active="EDITOR")
    request = GrantsReplace.model_validate({
        "operation_id": uuid4(), "expected_revision": 2,
        "grants": [{"subject_type": "role", "subject_id": "EDITOR", "permissions": ["view"]}],
    })
    async with viz.transaction(manager) as (repo, _):
        receipt = await repo.replace_grants(identity, request)
    async with viz.transaction(manager) as (repo, _):
        assert await repo.receipt(identity, request.operation_id) == receipt
        assert await repo.permissions(identity) == ["view"]


async def test_widget_filter_cannot_borrow_another_accessible_dataset_binding(viz):
    source, _, board = await graph(viz)
    unrelated = dataset()
    async with viz.transaction() as (repo, _):
        await repo.create(viz.workspace_id, unrelated, operation_id=uuid4())
    raw = board.model_dump(mode="json")
    widget = raw["pages"][0]["widgets"][0]
    widget["filters"] = {"expression": {
        "type": "predicate", "field": {"dataset_id": str(source.dataset_id),
            "dataset_revision": 1, "field_id": "region"},
        "op": "eq", "values": [{"type": "string", "value": "Jakarta"}],
    }}
    async with viz.transaction() as (repo, _):
        await repo.replace(DashboardDefinition.model_validate(raw),
                           operation_id=uuid4(), expected_revision=1)
    widget["filters"]["expression"]["field"]["dataset_id"] = str(unrelated.dataset_id)
    with pytest.raises(ValueError, match="pinned chart dataset"):
        async with viz.transaction() as (repo, _):
            await repo.replace(DashboardDefinition.model_validate(raw),
                               operation_id=uuid4(), expected_revision=2)
    async with viz.transaction() as (repo, _):
        assert (await repo.read(board.dashboard_id, draft=True))["revision"] == 2


async def test_direct_head_change_without_receipt_rolls_back(viz):
    document, _, _ = await create(viz)
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (_, connection):
            await connection.execute("UPDATE nova_meta.viz_resources "
                                     "SET metadata_revision=metadata_revision+1")
    rows = await viz.inspect("SELECT metadata_revision FROM nova_meta.viz_resources")
    assert rows[0]["metadata_revision"] == 1


async def test_editor_cannot_publish_or_change_grants_even_with_direct_sql(viz):
    document, _, _ = await create(viz)
    await grant(viz, document.dashboard_id, {"subject_type": "role", "subject_id": "EDITOR",
                                           "permissions": ["edit"]})
    editor = session("editor", active="EDITOR")
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(editor) as (repo, _):
            await repo.publish(document.dashboard_id,
                               PublicationRequest(operation_id=uuid4(), expected_revision=1))
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(editor) as (_, connection):
            await connection.execute(
                "INSERT INTO nova_meta.viz_acl_grants"
                "(resource_id,subject_type,subject_id,permission) "
                "VALUES (%s,'user','editor','manage')", (document.dashboard_id,),
            )


async def test_manager_can_replace_acl_and_remove_their_own_access_atomically(viz):
    document, _, _ = await create(viz)
    await grant(viz, document.dashboard_id, {
        "subject_type": "role", "subject_id": "EDITOR", "permissions": ["manage"],
    })
    request = GrantsReplace.model_validate({
        "operation_id": str(uuid4()), "expected_revision": 2,
        "grants": [{"subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"]}],
    })
    async with viz.transaction(session("manager", active="EDITOR")) as (repo, _):
        receipt = await repo.replace_grants(document.dashboard_id, request)
        assert receipt["event_sequence"] == 3
    async with viz.transaction(session("manager", active="EDITOR")) as (repo, _):
        with pytest.raises(NovaException):
            await repo.read(document.dashboard_id, draft=True)
    rows = await viz.inspect("SELECT subject_id,permission FROM nova_meta.viz_acl_grants")
    assert rows == [{"subject_id": "VIEWER", "permission": "view"}]


async def test_acl_replacement_updates_expiry_without_duplicate_or_lost_grants(viz):
    document, _, _ = await create(viz)
    original = {"subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"]}
    await grant(viz, document.dashboard_id, original)
    expires = datetime.now(UTC) + timedelta(hours=1)
    await grant(viz, document.dashboard_id,
                {**original, "expires_at": expires.isoformat()}, revision=2)
    rows = await viz.inspect("SELECT subject_id,expires_at FROM nova_meta.viz_acl_grants")
    assert rows == [{"subject_id": "VIEWER", "expires_at": expires}]


async def test_future_revision_cannot_piggyback_on_a_valid_edit_receipt(viz):
    document, _, _ = await create(viz)
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (repo, connection):
            await repo.replace(document.model_copy(update={"title": "Change"}),
                               operation_id=uuid4(), expected_revision=1)
            await connection.execute(
                "INSERT INTO nova_meta.viz_revisions"
                "(resource_id,revision,schema_version,definition,digest) "
                "SELECT resource_id,99,schema_version,definition,digest "
                "FROM nova_meta.viz_revisions WHERE revision=1",
            )
    assert (await viz.inspect("SELECT draft_revision FROM nova_meta.viz_resources"))[0] == {
        "draft_revision": 1,
    }


async def test_receipt_without_a_head_transition_and_event_is_rejected(viz):
    document, _, _ = await create(viz)
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (_, connection):
            await connection.execute(
                "INSERT INTO nova_meta.viz_operations"
                "(operation_id,resource_id,request_digest,kind,base_revision,"
                "new_revision,event_sequence) VALUES (%s,%s,%s,'edit',1,1,2)",
                (uuid4(), document.dashboard_id, "a" * 64),
            )
    assert (await viz.inspect("SELECT count(*) AS n FROM nova_meta.viz_operations"))[0]["n"] == 1


async def test_published_references_cannot_be_appended_in_a_later_transaction(viz):
    document, _, _ = await create(viz)
    other, _, _ = await create(viz)
    async with viz.transaction() as (repo, _):
        await repo.publish(document.dashboard_id,
                           PublicationRequest(operation_id=uuid4(), expected_revision=1))
    async with viz.transaction() as (repo, _):
        publication_id = (await repo.read(document.dashboard_id))["publication_id"]
    with pytest.raises(MetadataIntegrityViolation):
        async with viz.transaction() as (_, connection):
            await connection.execute(
                "INSERT INTO nova_meta.viz_publication_references"
                "(publication_id,resource_id,revision) VALUES (%s,%s,1)",
                (publication_id, other.dashboard_id),
            )


async def test_cancellation_rolls_back_definition_receipt_outbox_and_audit(viz):
    with pytest.raises(asyncio.CancelledError):
        async with viz.transaction() as (repo, _):
            await repo.create(viz.workspace_id, dashboard(), operation_id=uuid4())
            raise asyncio.CancelledError()
    for table in ("viz_resources", "viz_revisions", "viz_operations", "viz_outbox"):
        assert (await viz.inspect(f"SELECT count(*) AS n FROM nova_meta.{table}"))[0]["n"] == 0
    assert await viz.inspect("SELECT 1 FROM nova_meta.audit_log "
                             "WHERE metadata_table='viz_resources'") == []


@pytest.mark.parametrize("workload", ["query", "agent"])
async def test_read_workloads_cannot_mutate_native_documents(viz, workload):
    document, _, _ = await create(viz)
    async with viz.transaction(workload=workload) as (repo, _):
        assert (await repo.read(document.dashboard_id, draft=True))["definition"] == document
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction(workload=workload) as (_, connection):
            await connection.execute(
                "INSERT INTO nova_meta.viz_resources(resource_id,workspace_id,kind) "
                "VALUES (%s,%s,'dashboard')", (uuid4(), viz.workspace_id),
            )


async def test_query_semantic_metadata_is_published_scoped_and_read_only(viz):
    actor = SecurityContext.from_session(session("owner"))
    identity = str(uuid4())
    async with viz.transaction() as (_, connection):
        repo = IntelligenceMetadataRepository(connection, actor)
        await repo.create_view(catalog="default_catalog", database="analytics", schema="",
                               name="native_semantic", view_id=identity, definition={"v": 1},
                               fingerprint="a" * 64, visibility="PRIVATE")
    async with viz.transaction(workload="query") as (_, connection):
        repo = IntelligenceMetadataRepository(connection, actor)
        assert await repo.get_view(identity) is None
        assert await repo.get_version(identity, 1) is None
    async with viz.transaction() as (_, connection):
        repo = IntelligenceMetadataRepository(connection, actor)
        await repo.record_validation(identity, 1, {"valid": True})
        await repo.publish(identity, 1, expected_revision=1, expected_active=None)
        await repo.add_version(identity, {"v": 2}, "b" * 64, expected_latest=1)
    async with viz.transaction(workload="query") as (_, connection):
        repo = IntelligenceMetadataRepository(connection, actor)
        assert (await repo.get_view(identity))["active_version"] == 1
        assert (await repo.get_version(identity, 1))["definition"] == {"v": 1}
        assert await repo.get_version(identity, 2) is None
        await connection.execute("SELECT count(*) FROM nova_meta.config_entities")
    reader = session("reader")
    async with viz.transaction(reader, workload="query") as (_, connection):
        repo = IntelligenceMetadataRepository(connection, SecurityContext.from_session(reader))
        assert await repo.get_view(identity) is None
        assert await repo.get_version(identity, 1) is None
    async with viz.transaction() as (_, connection):
        await connection.execute(
            "UPDATE nova_meta.config_semantic_views SET visibility='PUBLIC',"
            "metadata_revision=metadata_revision+1 WHERE id=%s", (identity,),
        )
        repo = IntelligenceMetadataRepository(connection, actor)
        await repo.record_validation(identity, 2, {"valid": True})
        await repo.publish(identity, 2, expected_revision=3, expected_active=1)
    async with viz.transaction(reader, workload="query") as (_, connection):
        repo = IntelligenceMetadataRepository(connection, SecurityContext.from_session(reader))
        assert (await repo.get_version(identity, 1))["status"] == "DEPRECATED"
        assert (await repo.get_version(identity, 2))["status"] == "ACTIVE"
        await connection.execute("SELECT set_config('nova.service_identity','nova_meta_web',true)")
        cursor = await connection.execute("SELECT id FROM nova_meta.config_semantic_views")
        rows = await cursor.fetchall()
        assert rows == []
    for table in ("config_semantic_views", "config_semantic_view_versions", "config_entities"):
        with pytest.raises(MetadataPermissionDenied):
            async with viz.transaction(workload="query") as (_, connection):
                await connection.execute(f"DELETE FROM nova_meta.{table}")
    async with viz.transaction() as (_, connection):
        await IntelligenceMetadataRepository(connection, actor).deprecate(
            identity, expected_revision=4,
        )
    async with viz.transaction(workload="query") as (_, connection):
        repo = IntelligenceMetadataRepository(connection, actor)
        assert await repo.get_view(identity) is None
        assert await repo.get_version(identity, 1) is None


async def graph(viz):
    source = dataset()
    figure = chart(source, dataset_revision=1)
    board = DashboardDefinition.model_validate({
        "dashboard_id": str(uuid4()), "title": "Pinned sales", "pages": [{
            "page_id": str(uuid4()), "title": "Sales", "widgets": [{
                "kind": "chart", "widget_id": str(uuid4()),
                "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
                "chart_ref": {"chart_id": str(figure.chart_id), "revision": 1},
            }],
        }],
    })
    for document in (source, figure, board):
        async with viz.transaction() as (repo, _):
            await repo.create(viz.workspace_id, document, operation_id=uuid4())
    return source, figure, board


async def test_publication_pins_transitive_definitions_while_drafts_change(viz):
    source, figure, board = await graph(viz)
    async with viz.transaction() as (repo, _):
        await repo.publish(board.dashboard_id,
                           PublicationRequest(operation_id=uuid4(), expected_revision=1))
    async with viz.transaction() as (repo, _):
        await repo.replace(figure.model_copy(update={"title": "New draft"}),
                           operation_id=uuid4(), expected_revision=1)
        await repo.replace(source.model_copy(update={"title": "New dataset draft"}),
                           operation_id=uuid4(), expected_revision=1)
    refs = await viz.inspect(
        "SELECT resource_id,revision FROM nova_meta.viz_publication_references",
    )
    assert {(row["resource_id"], row["revision"]) for row in refs} == {
        (source.dataset_id, 1), (figure.chart_id, 1),
    }
    for identity in (source.dataset_id, figure.chart_id, board.dashboard_id):
        await grant(viz, identity, {
            "subject_type": "role", "subject_id": "VIEWER", "permissions": ["view"],
        })
    async with viz.transaction(session("viewer", active="VIEWER")) as (_, connection):
        revisions = await connection.execute(
            "SELECT resource_id,revision FROM nova_meta.viz_revisions",
        )
        assert {(row["resource_id"], row["revision"]) for row in await revisions.fetchall()} == {
            (source.dataset_id, 1), (figure.chart_id, 1), (board.dashboard_id, 1),
        }


async def test_chart_cannot_bind_fields_absent_from_the_pinned_dataset(viz):
    source, figure, _ = await graph(viz)
    definition = figure.model_dump()
    definition["encoding"]["measures"][0]["field_id"] = "missing"
    invalid = type(figure).model_validate(definition)
    with pytest.raises(ValueError, match="absent"):
        async with viz.transaction() as (repo, _):
            await repo.replace(invalid, operation_id=uuid4(), expected_revision=1)
    async with viz.transaction() as (repo, _):
        assert (await repo.read(figure.chart_id, draft=True))["revision"] == 1


async def test_chart_cannot_reference_a_dataset_in_another_workspace(viz):
    source = dataset()
    async with viz.transaction(repository_type=WorkspaceSharingRepository) as (spaces, _):
        other_space = await spaces.create("Other workspace")
    async with viz.transaction() as (repo, _):
        await repo.create(other_space["id"], source, operation_id=uuid4())
    with pytest.raises(MetadataPermissionDenied):
        async with viz.transaction() as (repo, _):
            await repo.create(viz.workspace_id, chart(source, dataset_revision=1),
                              operation_id=uuid4())


async def test_storage_revalidates_internal_model_copies_before_persistence(viz):
    document, _, _ = await create(viz)
    invalid = document.model_copy(update={"title": ""})
    with pytest.raises(ValidationError):
        async with viz.transaction() as (repo, _):
            await repo.replace(invalid, operation_id=uuid4(), expected_revision=1)
    async with viz.transaction() as (repo, _):
        assert (await repo.read(document.dashboard_id, draft=True))["definition"] == document


async def test_viewer_reads_only_the_revisions_a_visible_publication_pins(viz):
    source, figure, board = await graph(viz)
    for identity in (source.dataset_id, figure.chart_id, board.dashboard_id):
        async with viz.transaction() as (repository, _):
            await repository.replace_grants(identity, GrantsReplace.model_validate({
                "operation_id": uuid4(), "expected_revision": 1, "grants": [
                    {"subject_type": "user", "subject_id": "reader", "permissions": ["view"]},
                ],
            }))
    async with viz.transaction() as (repository, _):
        await repository.publish(board.dashboard_id, PublicationRequest(
            operation_id=uuid4(), expected_revision=1,
        ))
    # A later chart draft is not part of the publication the viewer can see.
    async with viz.transaction() as (repository, _):
        await repository.replace(figure.model_copy(update={"title": "Unpublished edit"}),
                                 operation_id=uuid4(), expected_revision=1)
    reader = session("reader", active="VIEWER")
    async with viz.transaction(reader) as (repository, _):
        pinned = await repository.read(figure.chart_id, revision=1)
        assert pinned["revision"] == 1 and pinned["definition"].title == figure.title
        assert (await repository.read(source.dataset_id, revision=1))["revision"] == 1
    with pytest.raises(NovaException) as unpublished:
        async with viz.transaction(reader) as (repository, _):
            await repository.read(figure.chart_id, revision=2)
    assert unpublished.value.status_code == 404
    with pytest.raises(NovaException) as draft:
        async with viz.transaction(reader) as (repository, _):
            await repository.read(figure.chart_id, draft=True, revision=1)
    assert draft.value.status_code in {403, 404}
    stranger = session("stranger", active="VIEWER")
    with pytest.raises(NovaException):
        async with viz.transaction(stranger) as (repository, _):
            await repository.read(figure.chart_id, revision=1)


async def _saves(viz, dashboard_id):
    rows = await viz.inspect(
        "SELECT r.draft_revision, count(o.operation_id) AS operations "
        "FROM nova_meta.viz_resources r LEFT JOIN nova_meta.viz_operations o "
        "ON o.resource_id=r.resource_id WHERE r.resource_id=%s GROUP BY r.draft_revision",
        (dashboard_id,))
    return rows[0]


async def test_save_interrupted_by_a_database_restart_is_neither_lost_nor_doubled(
        native_http, viz):
    """NDV-OPS-001: every client connection drops while a save waits to commit."""
    client, records = native_http
    owner = records["login-owner"]
    document = dashboard()
    assert (await http_create(client, owner, document)).status_code == 201
    path = f"/api/v1/viz/dashboards/{document.dashboard_id}"
    key = uuid4()
    body = document.model_copy(update={"title": "Saved across a restart"}).model_dump(mode="json")

    def save():
        return client.patch(path + "/draft", json=body,
                            headers=http_headers(owner, key=key, revision=1))

    async with await psycopg.AsyncConnection.connect(
        viz.environment.config.dsn, autocommit=True,
    ) as blocker, blocker.transaction():
        await blocker.execute("SELECT 1 FROM nova_meta.viz_resources WHERE resource_id=%s "
                              "FOR UPDATE", (document.dashboard_id,))
        pending = asyncio.create_task(save())
        for _ in range(200):  # Wait until the save is inside its transaction.
            waiting = await viz.inspect(
                "SELECT pid FROM pg_stat_activity WHERE usename='nova_meta_web' "
                "AND wait_event_type='Lock'")
            if waiting:
                break
            await asyncio.sleep(0.05)
        assert waiting, "the save never reached the database"
        # A primary restart ends every client session, mid-transaction included.
        await viz.inspect("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                          "WHERE usename='nova_meta_web'")
        interrupted = await pending
    assert interrupted.status_code == 503, interrupted.text
    # Nothing was acknowledged, nothing was written, and the receipt says so.
    assert await _saves(viz, document.dashboard_id) == {"draft_revision": 1, "operations": 1}
    receipt = await client.get(path + f"/operations/{key}", headers=http_headers(owner))
    assert receipt.status_code == 404
    # The pool reconnects; the same operation then applies exactly once.
    retried = await save()
    assert retried.status_code == 200, retried.text
    assert retried.json()["new_revision"] == 2
    assert (await save()).json() == retried.json()
    assert await _saves(viz, document.dashboard_id) == {"draft_revision": 2, "operations": 2}
    draft = (await client.get(path + "?draft=true", headers=http_headers(owner))).json()
    assert draft["definition"]["title"] == "Saved across a restart"


async def test_save_whose_acknowledgement_is_lost_is_resolved_by_its_receipt(
        native_http, viz, monkeypatch):
    """NDV-OPS-001: the commit happened but the connection dropped before the reply."""
    client, records = native_http
    owner = records["login-owner"]
    document = dashboard()
    assert (await http_create(client, owner, document)).status_code == 201
    path = f"/api/v1/viz/dashboards/{document.dashboard_id}"
    database = metadata_scope.metadata_db()
    original = database.transaction
    lose_next = {"armed": True}

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        async with original(*args, **kwargs) as connection:
            yield connection
        if lose_next["armed"] and not kwargs.get("read_only"):
            lose_next["armed"] = False
            raise MetadataOutcomeUnknown()  # As if COMMIT's reply never arrived.

    monkeypatch.setattr(database, "transaction", transaction)
    key = uuid4()
    body = document.model_copy(update={"title": "Committed, reply lost"}).model_dump(mode="json")
    lost = await client.patch(path + "/draft", json=body,
                              headers=http_headers(owner, key=key, revision=1))
    assert lost.status_code == 503
    receipt = await client.get(path + f"/operations/{key}", headers=http_headers(owner))
    assert receipt.status_code == 200 and receipt.json()["new_revision"] == 2
    retried = await client.patch(path + "/draft", json=body,
                                 headers=http_headers(owner, key=key, revision=1))
    assert retried.status_code == 200 and retried.json() == receipt.json()
    # A different save still based on revision 1 is refused, not stacked as a duplicate.
    stale = await client.patch(path + "/draft", json=body,
                               headers=http_headers(owner, key=uuid4(), revision=1))
    assert stale.status_code == 409
    assert await _saves(viz, document.dashboard_id) == {"draft_revision": 2, "operations": 2}
