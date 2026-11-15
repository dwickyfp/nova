"""Native commands revalidate the login and never widen the acting identity."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import NovaException, SessionExpiredError
from app.core.metadata_db import MetadataPermissionDenied
from app.modules.viz import document_service as module
from app.modules.viz.contracts.commands import GrantsReplace
from app.modules.viz.contracts.dashboards import DashboardDefinition, Page
from app.modules.viz.contracts.datasets import DatasetDefinition
from app.modules.viz.contracts.http import DocumentSnapshot


@pytest.mark.parametrize("draft,revision", [(False, None), (True, None), (True, 1)])
async def test_publication_excludes_private_sql_and_formulas_while_draft_retains_them(
    login, monkeypatch, draft, revision,
):
    user, _ = login
    identity = uuid4()
    definition = DatasetDefinition.model_validate({
        "dataset_id": str(identity), "title": "Published revenue",
        "source": {
            "kind": "sql", "sql": "SELECT 'private-source-marker' AS amount FROM orders",
            "ast_digest": "a" * 64, "catalog": "default_catalog", "database": "analytics",
            "dependencies": [{"kind": "table", "catalog": "default_catalog",
                              "database": "analytics", "object_name": "orders",
                              "physical_id": "1001"}],
        },
        "fields": [{"field_id": "amount", "name": "Amount", "source_column": "amount",
                    "logical_type": "decimal", "physical_type": "DECIMAL(38,12)",
                    "nullable": True, "semantic_role": "measure"}],
        "metrics": [{"metric_id": str(uuid4()), "revision": 1, "name": "Revenue",
                     "formula": "SUM([amount]) /* private-expression-marker */",
                     "level": "aggregate", "grain": [], "unit": "currency",
                     "currency": "IDR", "scope": "dashboard_local"}],
        "schema_fingerprint": "b" * 64,
    })
    original = {
        "resource_id": identity, "revision": 1, "metadata_revision": 1,
        "grants_revision": 1, "publication_id": uuid4(), "definition": definition,
    }
    repository = SimpleNamespace(read=AsyncMock(return_value=original))

    @asynccontextmanager
    async def transaction(*args, **kwargs):
        assert kwargs["read_only"]
        yield repository

    monkeypatch.setattr(module, "document_transaction", transaction)
    result = await module.NativeDocumentService().read(
        user, identity, draft=draft, revision=revision,
    )
    serialized = DocumentSnapshot.model_validate(result).model_dump_json()
    assert ("private-source-marker" in serialized) is draft
    assert ("private-expression-marker" in serialized) is draft
    assert "Published revenue" in serialized and "Revenue" in serialized
    assert original["definition"] is definition
    assert definition.source.sql.startswith("SELECT")
    if not draft:
        assert '"source"' not in serialized and '"formula"' not in serialized
        assert '"schema_fingerprint"' not in serialized
        assert result["definition"].definition_scope == "publication"
    repository.read.assert_awaited_once_with(identity, draft=draft, revision=revision)


@pytest.fixture
def login(monkeypatch):
    user = {"username": "alice", "active_role": "ANALYST",
            "assigned_roles": ["ANALYST", "EDITOR"], "session_id": "alice-login",
            "security_context_version": 5}
    get = AsyncMock(return_value={**user, "encrypted_password": "private-password-envelope"})
    monkeypatch.setattr(module.session_store, "get", get)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())
    return user, get


async def test_session_binding_uses_current_assigned_roles_without_credentials(login):
    user, get = login
    get.return_value["assigned_roles"] = ["ANALYST"]
    result = await module.current_session(user)
    assert result["assigned_roles"] == ["ANALYST"]
    assert result["session_id"] == user["session_id"]
    assert "encrypted_password" not in result
    get.assert_awaited_once_with(user["session_id"])


@pytest.mark.parametrize("changes", [
    {"username": "bob"}, {"active_role": "EDITOR"}, {"security_context_version": 6},
    {"assigned_roles": ["EDITOR"]}, {"active_role": "ALL"}, {"active_role": None},
    {"security_context_version": None}, {"security_context_version": True},
    {"security_context_version": "5"},
    {"assigned_roles": [], "roles": ["ANALYST"]},
    {"assigned_roles": None, "roles": ["ANALYST"]},
    {"assigned_roles": "ANALYST"}, {"assigned_roles": {"ANALYST": True}},
    {"assigned_roles": ["ANALYST", None]}, {"assigned_roles": ["ANALYST", " EDITOR"]},
])
async def test_changed_or_incomplete_security_context_is_refused(login, changes):
    user, get = login
    get.return_value.update(changes)
    with pytest.raises(MetadataPermissionDenied):
        await module.current_session(user)


async def test_expired_session_and_absent_login_cannot_open_metadata(login):
    user, get = login
    get.return_value = None
    with pytest.raises(SessionExpiredError):
        await module.current_session(user)
    get.reset_mock()
    with pytest.raises(SessionExpiredError):
        await module.current_session({**user, "session_id": None})
    get.assert_not_awaited()


async def test_missing_canonical_role_assignment_cannot_use_legacy_roles(login):
    user, get = login
    get.return_value.pop("assigned_roles")
    get.return_value["roles"] = ["ANALYST"]
    with pytest.raises(MetadataPermissionDenied):
        await module.current_session(user)


def grants(subject="alice", kind="user"):
    return GrantsReplace.model_validate({
        "operation_id": uuid4(), "expected_revision": 1,
        "grants": [{"subject_type": kind, "subject_id": subject, "permissions": ["view"]}],
    })


async def test_directory_is_not_read_before_document_management_is_authorized(login, monkeypatch):
    user, _ = login
    service = module.NativeDocumentService()
    monkeypatch.setattr(service, "grants", AsyncMock(side_effect=MetadataPermissionDenied()))
    directory = AsyncMock()
    monkeypatch.setattr(module.user_service, "list_users", directory)
    with pytest.raises(MetadataPermissionDenied):
        await service.replace_grants(user, uuid4(), grants())
    directory.assert_not_awaited()


async def test_unknown_grant_subject_is_refused_without_echoing_the_identifier(login, monkeypatch):
    user, _ = login
    service = module.NativeDocumentService()
    monkeypatch.setattr(service, "grants", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.user_service, "list_users", AsyncMock(return_value=[]))
    with pytest.raises(NovaException) as error:
        await service.replace_grants(user, uuid4(), grants("hidden-directory-subject"))
    assert error.value.status_code == 422
    assert "hidden-directory-subject" not in str(error.value)


async def test_role_switch_during_directory_lookup_prevents_the_write(login, monkeypatch):
    user, get = login
    service = module.NativeDocumentService()
    monkeypatch.setattr(service, "grants", AsyncMock(return_value=[]))

    async def directory():
        get.return_value.update(active_role="EDITOR", security_context_version=6)
        return [{"name": "ANALYST"}]

    monkeypatch.setattr(module.user_service, "list_roles", directory)
    transaction = AsyncMock(side_effect=AssertionError("Must not open a metadata transaction"))
    monkeypatch.setattr(module, "metadata_transaction", transaction)
    with pytest.raises(MetadataPermissionDenied):
        await service.replace_grants(user, uuid4(), grants("ANALYST", "role"))
    transaction.assert_not_called()
    assert module.write_audit_log.call_args.kwargs["decision"] == "DENY"


async def test_refusal_audit_contains_identity_but_no_definitions_or_credentials(login):
    user, get = login
    get.return_value["security_context_version"] += 1
    identity = uuid4()
    with pytest.raises(MetadataPermissionDenied):
        await module.NativeDocumentService().read(user, identity)
    event = module.write_audit_log.call_args.kwargs
    assert event["object_name"] == str(identity)
    assert event["user_name"] == user["username"]
    assert event["active_role"] == user["active_role"]
    assert event["session_id"] == user["session_id"]
    assert "sql_text" not in event
    assert "private-password-envelope" not in str(event)


@pytest.mark.parametrize("action", ["LIST", "CREATE"])
@pytest.mark.parametrize("refusal", ["stale_session", "workspace"])
async def test_admission_refusals_are_audited_before_document_transaction(
    login, monkeypatch, action, refusal,
):
    user, get = login
    resolve = AsyncMock(side_effect=NovaException("Workspace unavailable", status_code=404))
    monkeypatch.setattr(module.space_service, "resolve", resolve)
    transaction = AsyncMock(side_effect=AssertionError("No document transaction expected"))
    monkeypatch.setattr(module, "metadata_transaction", transaction)
    if refusal == "stale_session":
        get.return_value["security_context_version"] += 1
    document = DashboardDefinition(dashboard_id=uuid4(), title="private-title-canary",
                                   pages=(Page(page_id=uuid4(), title="Page 1"),))
    service = module.NativeDocumentService()
    with pytest.raises(NovaException) as error:
        if action == "LIST":
            await service.browse(user, "private-workspace-canary")
        else:
            await service.create_visual(user, "private-workspace-canary", document,
                                        operation_id=uuid4())
    assert error.value.status_code == (403 if refusal == "stale_session" else 404)
    transaction.assert_not_called()
    assert resolve.await_count == (0 if refusal == "stale_session" else 1)
    module.write_audit_log.assert_awaited_once()
    event = module.write_audit_log.call_args.kwargs
    assert event["action"] == action
    assert event["object_name"] == (
        str(document.dashboard_id) if action == "CREATE" else "document_collection"
    )
    assert event["decision"] == "DENY"
    assert event["user_name"] == user["username"]
    assert "private-" not in str(event)
