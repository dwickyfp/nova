"""HTTP admission bounds and redaction before document mutations."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.core.deps import get_current_user
from app.core.exceptions import register_exception_handlers
from app.modules.viz import router as module
from app.modules.viz.contracts.authoring import DatasetPreview, ObjectInspection
from app.modules.viz.contracts.dashboards import DashboardDefinition, Page
from app.modules.viz.contracts.datasets import DatasetField


@pytest.fixture
def document():
    return DashboardDefinition(
        dashboard_id=uuid4(), title="Operations", pages=(Page(page_id=uuid4(), title="Overview"),)
    )


@pytest.fixture
async def api(monkeypatch, document):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(module.router, prefix="/viz")
    user = {
        "username": "alice",
        "active_role": "ANALYST",
        "session_id": "alice-login",
        "assigned_roles": ["ANALYST"],
        "security_context_version": 1,
    }
    app.dependency_overrides[get_current_user] = lambda: user
    service = SimpleNamespace(
        create_visual=AsyncMock(),
        require_kind=AsyncMock(),
        read=AsyncMock(),
        dashboard_datasets=AsyncMock(),
        browse=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(module, "service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://native.test"
    ) as client:
        yield client, service, app


def payload(document):
    return {"workspace_id": "personal", "definition": document.model_dump(mode="json")}


@pytest.mark.parametrize(
    "revision", ["0", "-1", "1.2", "true", "9007199254740992", "private-input"],
)
async def test_historical_read_validates_revision_before_service(api, document, revision):
    client, service, _ = api
    response = await client.get(
        f"/viz/dashboards/{document.dashboard_id}?draft=true&revision={revision}",
    )
    assert response.status_code == 422
    assert "private-input" not in response.text
    service.read.assert_not_awaited()


async def test_historical_read_forwards_explicit_revision_and_matching_etag(api, document):
    client, service, _ = api
    service.read.return_value = {
        "resource_id": document.dashboard_id, "revision": 2,
        "metadata_revision": 5, "grants_revision": 1,
        "publication_id": None, "definition": document,
    }
    response = await client.get(f"/viz/dashboards/{document.dashboard_id}?draft=true&revision=2")
    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    assert service.read.call_args.kwargs == {"draft": True, "revision": 2}


async def test_dataset_index_requires_revision_and_returns_only_authorized_labels(api, document):
    client, service, _ = api
    path = f"/viz/dashboards/{document.dashboard_id}/datasets"
    missing = await client.get(path)
    assert missing.status_code == 422
    service.dashboard_datasets.assert_not_awaited()
    service.dashboard_datasets.return_value = {
        "dashboard_id": document.dashboard_id, "revision": 1,
        "datasets": [{"dataset_id": uuid4(), "revision": 2, "title": None}],
    }
    response = await client.get(path + "?revision=1")
    assert response.status_code == 200
    assert response.json()["datasets"][0]["title"] is None
    assert response.headers["Cache-Control"] == "no-store"
    assert service.dashboard_datasets.call_args.args[0]["username"] == "alice"


@pytest.mark.parametrize("key", [None, "invalid-private-key", "", "x" * 2048])
async def test_creation_requires_bounded_uuid_idempotency_key(api, document, key):
    client, service, _ = api
    headers = {"Idempotency-Key": key} if key is not None else {}
    response = await client.post("/viz/dashboards", json=payload(document), headers=headers)
    assert response.status_code == (428 if key is None else 422)
    assert "invalid-private-key" not in response.text
    service.create_visual.assert_not_awaited()


@pytest.mark.parametrize(
    "value", [None, "1", 'W/"1"', '"0"', '"01"', '"-1"', '"9007199254740992"', '"1", "2"', "*"]
)
async def test_replacement_requires_one_strong_bounded_revision(api, document, value):
    client, service, _ = api
    headers = {"Idempotency-Key": str(uuid4())}
    if value is not None:
        headers["If-Match"] = value
    response = await client.patch(
        f"/viz/dashboards/{document.dashboard_id}/draft",
        json=document.model_dump(mode="json"),
        headers=headers,
    )
    assert response.status_code == (428 if value is None else 422)
    service.require_kind.assert_not_awaited()


@pytest.mark.parametrize("revision", [None, 'W/"1"'])
async def test_dataset_source_edits_require_a_strong_revision(api, monkeypatch, revision):
    client, _, _ = api
    update = AsyncMock()
    monkeypatch.setattr(module.datasets, "update", update)
    identity = uuid4()
    headers = {"Idempotency-Key": str(uuid4())}
    if revision is not None:
        headers["If-Match"] = revision
    response = await client.patch(f"/viz/datasets/{identity}/draft", headers=headers, json={
        "dataset_id": str(identity), "title": "Reviewed source",
        "source": {"kind": "sql", "database": "analytics", "sql": "SELECT id FROM orders"},
        "expected_schema_fingerprint": "a" * 64,
    })
    assert response.status_code == (428 if revision is None else 422)
    update.assert_not_awaited()


async def test_dataset_source_update_cannot_change_identity(api, monkeypatch):
    client, _, _ = api
    update = AsyncMock()
    monkeypatch.setattr(module.datasets, "update", update)
    response = await client.patch(f"/viz/datasets/{uuid4()}/draft", headers={
        "Idempotency-Key": str(uuid4()), "If-Match": '"1"',
    }, json={
        "dataset_id": str(uuid4()), "title": "Reviewed source",
        "source": {"kind": "sql", "database": "analytics", "sql": "SELECT id FROM orders"},
        "expected_schema_fingerprint": "a" * 64,
    })
    assert response.status_code == 422
    update.assert_not_awaited()


async def test_chunked_oversized_body_is_rejected_before_mutation(api):
    client, service, _ = api

    async def chunks():
        for _ in range(17):
            yield b" " * 65536

    response = await client.post(
        "/viz/dashboards",
        content=chunks(),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert response.status_code == 413
    service.create_visual.assert_not_awaited()


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
async def test_mutations_require_json(api, content_type):
    client, service, _ = api
    response = await client.post(
        "/viz/dashboards",
        content=b"{}",
        headers={
            "Content-Type": content_type,
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert response.status_code == 415
    service.create_visual.assert_not_awaited()


async def test_validation_does_not_echo_secret_input_or_accept_actor_spoofing(api, document):
    client, service, _ = api
    body = {**payload(document), "actor": "ACCOUNTADMIN", "password": "private-input-marker"}
    response = await client.post(
        "/viz/dashboards", json=body, headers={"Idempotency-Key": str(uuid4())}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid document request"}
    assert response.headers["Cache-Control"] == "no-store"
    service.create_visual.assert_not_awaited()


async def test_malformed_json_does_not_echo_input(api):
    client, _, _ = api
    response = await client.post(
        "/viz/dashboards",
        content=b'{"private-marker":',
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert "private-marker" not in response.text


async def test_creation_returns_only_typed_receipt_and_passes_authenticated_actor(api, document):
    client, service, _ = api
    key = uuid4()
    service.create_visual.return_value = {
        "operation_id": key,
        "resource_id": document.dashboard_id,
        "base_revision": 0,
        "new_revision": 1,
        "event_sequence": 1,
        "committed_at": datetime.now(UTC),
    }
    response = await client.post(
        "/viz/dashboards", json=payload(document), headers={"Idempotency-Key": str(key)}
    )
    assert response.status_code == 201
    assert response.json()["operation_id"] == str(key)
    assert response.headers["Cache-Control"] == "no-store"
    assert service.create_visual.call_args.args[0]["username"] == "alice"


async def test_dataset_inspection_uses_authenticated_identity_and_server_schema(api, monkeypatch):
    client, _, _ = api
    identity = uuid4()
    result = ObjectInspection.model_validate(
        {
            "dataset_id": identity,
            "source": {
                "kind": "table",
                "catalog": "default_catalog",
                "database": "analytics",
                "object_name": "sales",
                "physical_id": "10751",
            },
            "fields": [
                {
                    "field_id": str(uuid4()),
                    "name": "amount",
                    "source_column": "amount",
                    "logical_type": "decimal",
                    "physical_type": "decimal(38,12)",
                    "nullable": True,
                    "semantic_role": "measure",
                }
            ],
            "schema_fingerprint": "a" * 64,
        }
    )
    inspect = AsyncMock(return_value=result)
    monkeypatch.setattr(module.datasets, "inspect", inspect)
    response = await client.post(
        "/viz/datasets/inspect",
        json={
            "dataset_id": str(identity),
            "source": {"kind": "table", "database": "analytics", "object_name": "sales"},
        },
    )
    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")
    assert response.headers["Cache-Control"] == "no-store"
    assert inspect.call_args.args[0]["username"] == "alice"


async def test_dataset_create_refuses_client_owned_schema(api, monkeypatch):
    client, _, _ = api
    create = AsyncMock()
    monkeypatch.setattr(module.datasets, "create", create)
    response = await client.post(
        "/viz/datasets",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "dataset_id": str(uuid4()),
            "workspace_id": "personal",
            "title": "Sales",
            "source": {"kind": "table", "database": "analytics", "object_name": "sales"},
            "expected_physical_id": "100",
            "expected_schema_fingerprint": "a" * 64,
            "fields": [{"private-schema-marker": True}],
        },
    )
    assert response.status_code == 422
    assert "private-schema-marker" not in response.text
    create.assert_not_awaited()


async def test_preview_response_is_typed_and_uses_authenticated_caller(api, monkeypatch):
    client, _, _ = api
    identity = uuid4()
    result = DatasetPreview(
        dataset_id=identity,
        revision=1,
        offset=0,
        has_more=False,
        fields=(
            DatasetField(
                field_id="value",
                name="Value",
                source_column="value",
                logical_type="integer",
                physical_type="bigint",
                semantic_role="measure",
                nullable=True,
            ),
        ),
        rows=(({"type": "integer", "value": "9223372036854775807"},), (None,)),
    )
    preview = AsyncMock(return_value=result)
    monkeypatch.setattr(module.datasets, "preview", preview)
    response = await client.post(f"/viz/datasets/{identity}/preview", json={"revision": 1})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == result.model_dump(mode="json")
    assert preview.call_args.args[0]["username"] == "alice"


@pytest.mark.parametrize(
    "body",
    [
        {"revision": 1, "limit": 201},
        {"revision": 1, "offset": 10001},
        {"revision": 1, "sql": "private-input-marker"},
    ],
)
async def test_preview_rejects_bounds_and_client_sql_without_echo(api, monkeypatch, body):
    client, _, _ = api
    preview = AsyncMock()
    monkeypatch.setattr(module.datasets, "preview", preview)
    response = await client.post(f"/viz/datasets/{uuid4()}/preview", json=body)
    assert response.status_code == 422 and "private-input-marker" not in response.text
    preview.assert_not_awaited()


async def test_dataset_collection_uses_dataset_kind(api):
    client, service, _ = api
    response = await client.get("/viz/datasets?workspace_id=personal&draft=true")
    assert response.status_code == 200
    assert service.browse.call_args.kwargs["kind"] == "dataset"


async def test_snapshot_has_revision_etag_and_no_cache(api, document):
    client, service, _ = api
    service.read.return_value = {
        "resource_id": document.dashboard_id,
        "revision": 3,
        "publication_id": None,
        "metadata_revision": 4,
        "grants_revision": 2,
        "definition": document,
    }
    response = await client.get(f"/viz/dashboards/{document.dashboard_id}?draft=true")
    assert response.status_code == 200
    assert response.headers["ETag"] == '"3"'
    assert response.headers["Cache-Control"] == "no-store"
    service.require_kind.assert_awaited_once()


@pytest.mark.parametrize(
    "query",
    [
        "limit=101",
        "before_time=2026-01-01T00:00:00Z",
        "before_id=invalid",
        "before_time=2026-01-01T00:00:00",
    ],
)
async def test_browse_cursor_and_page_limits_are_validated(api, query):
    client, service, _ = api
    response = await client.get("/viz/dashboards?workspace_id=personal&" + query)
    assert response.status_code == 422
    service.browse.assert_not_awaited()


async def test_anonymous_caller_cannot_list_documents(api):
    client, service, app = api
    app.dependency_overrides.clear()
    response = await client.get("/viz/dashboards?workspace_id=personal")
    assert response.status_code in {401, 403}
    service.browse.assert_not_awaited()


def test_native_router_is_registered_on_normal_application():
    from app.main import create_app

    assert "/api/v1/viz/dashboards" in create_app().openapi()["paths"]


@pytest.mark.parametrize("change,status", [
    ("missing_revision", 428), ("weak_revision", 422), ("workspace", 422), ("actor", 422),
])
async def test_dashboard_dataset_creation_refuses_invalid_authority_and_revision(
    api, monkeypatch, change, status,
):
    client, _, _ = api
    create = AsyncMock()
    monkeypatch.setattr(module.datasets, "create_in_dashboard", create)
    headers = {"Idempotency-Key": str(uuid4()), "If-Match": '"1"'}
    body = {"dataset": {"dataset_id": str(uuid4()), "title": "Private source",
                       "source": {"kind": "sql", "database": "analytics",
                                  "sql": "SELECT private_marker FROM orders"},
                       "expected_schema_fingerprint": "a" * 64}}
    if change == "missing_revision":
        del headers["If-Match"]
    elif change == "weak_revision":
        headers["If-Match"] = 'W/"1"'
    elif change == "workspace":
        body["dataset"]["workspace_id"] = "another-workspace"
    elif change == "actor":
        body["actor"] = "ACCOUNTADMIN"
    response = await client.post(f"/viz/dashboards/{uuid4()}/datasets", headers=headers, json=body)
    assert response.status_code == status
    assert "private_marker" not in response.text and "another-workspace" not in response.text
    create.assert_not_awaited()


async def test_an_export_is_checked_for_the_dashboard_kind_and_caller(api, document):
    client, service, _ = api
    service.authorize_export = AsyncMock(return_value={"allowed": True, "format": "xlsx"})
    chart = uuid4()
    response = await client.post(f"/viz/dashboards/{document.dashboard_id}/exports",
                                 json={"format": "xlsx", "chart_id": str(chart)})
    assert response.status_code == 200 and response.json()["allowed"] is True
    service.require_kind.assert_awaited_once()
    assert service.require_kind.await_args.args[1:] == (document.dashboard_id, "dashboard")
    kwargs = service.authorize_export.await_args.kwargs
    assert kwargs == {"file_format": "xlsx", "part": chart}


@pytest.mark.parametrize("body", [{"format": "exe"}, {"format": "csv", "sql": "SELECT 1"}, {}])
async def test_an_export_request_is_bounded(api, document, body):
    client, service, _ = api
    service.authorize_export = AsyncMock()
    response = await client.post(f"/viz/dashboards/{document.dashboard_id}/exports", json=body)
    assert response.status_code == 422 and "SELECT" not in response.text
    service.authorize_export.assert_not_awaited()


async def test_export_needs_the_export_permission_and_is_audited(monkeypatch):
    from app.modules.viz import document_service as documents

    audits: list[dict] = []

    async def audit(**kwargs):
        audits.append(kwargs)
        return "q"

    monkeypatch.setattr(documents, "write_audit_log", audit)
    service = documents.NativeDocumentService()
    user = {"username": "alice", "active_role": "ANALYST", "session_id": "s",
            "assigned_roles": ["ANALYST"], "security_context_version": 1}
    dashboard = uuid4()
    monkeypatch.setattr(service, "permissions", AsyncMock(return_value=["view", "export"]))
    assert (await service.authorize_export(user, dashboard, file_format="png"))["allowed"]
    monkeypatch.setattr(service, "permissions", AsyncMock(return_value=["view", "edit"]))
    with pytest.raises(module.NovaException) as refused:
        await service.authorize_export(user, dashboard, file_format="csv")
    assert refused.value.status_code == 403 and "downloading" in refused.value.message
    assert [(item["action"], item["decision"]) for item in audits] == [
        ("EXPORT_PNG", "ALLOW"), ("EXPORT_CSV", "DENY")]
    assert all(item["user_name"] == "alice" and item["active_role"] == "ANALYST"
               for item in audits)
