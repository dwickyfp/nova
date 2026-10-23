import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.smart_reports import details, distribution, security, templates
from app.modules.smart_reports.details import DetailRequest, validate_detail_fields
from tests.unit.test_smart_report_exports import snapshot
from tests.unit.test_smart_reports import USER, report_spec, view


def detail_model():
    return {
        "name": "sales",
        "datasets": [
            {
                "name": "sales",
                "source": "qa.sales",
                "primary_key": ["id"],
                "fields": [
                    {"name": "id", "dimension": {}},
                    {"name": "city", "dimension": {}},
                    {"name": "amount"},
                ],
            }
        ],
    }


def test_detail_requires_declared_fields_and_complete_published_grain():
    model = SemanticModelIR.from_ossie(detail_model())
    validate_detail_fields(model, ["id", "city"])
    for fields in (["city"], ["id", "secret"], ["id", "amount"]):
        with pytest.raises(ValueError):
            validate_detail_fields(model, fields)
    with pytest.raises(ValueError):
        DetailRequest(
            view_session_id="view",
            expected_state_revision=1,
            field="city",
            values=["Jakarta"],
            page_index=20,
        )


async def test_detail_reuses_caller_filters_and_checks_original_session(monkeypatch):
    spec, state = report_spec(), view()
    widget = spec.widgets()["cities"]
    widget.query.dimensions = ["city"]
    widget.detail_fields = ["id", "city"]
    original = state.model_copy(deep=True)
    service = SimpleNamespace(
        view=AsyncMock(return_value=(spec, state)), verify_view=AsyncMock(), audit=AsyncMock()
    )
    monkeypatch.setattr(details, "report_service", service)
    monkeypatch.setattr(
        details.semantic_view_service,
        "_readable_version",
        AsyncMock(return_value=({}, {"definition": detail_model()})),
    )
    from app.modules.smart_reports.runtime import report_runtime

    render = AsyncMock(return_value={"widgets": []})
    monkeypatch.setattr(report_runtime, "render", render)
    body = DetailRequest(
        view_session_id="view",
        expected_state_revision=1,
        field="city",
        values=["Jakarta"],
        page_index=2,
    )
    await details.read_detail("report-a", "cities", body, USER)
    query_spec, query_state, caller, selection = render.await_args.args
    assert caller == USER and selection == ["cities"]
    assert query_spec.widgets()["cities"].query.metrics == []
    assert query_state.filters == original.filters
    assert query_state.drill_paths["cities"][-1].value == ["Jakarta"]
    assert query_state.page_size == 100 and query_state.page_index["cities"] == 2
    service.verify_view.assert_awaited_once_with(original, USER)
    assert state == original and spec.widgets()["cities"].kind == "table"
    with pytest.raises(HTTPException):
        await details.read_detail(
            "report-a", "cities", body.model_copy(update={"field": "hidden"}), USER
        )


async def test_saved_activity_is_private_and_rechecks_accessible_rows(monkeypatch):
    saved = snapshot()
    encoded = json.dumps(saved).encode()
    payload = {
        "kind": "activity",
        "phase": "complete",
        "status": "delivered",
        "report_id": "report-a",
        "identity": distribution.identity(USER),
        "snapshot_uri": "private",
        "snapshot_checksum": hashlib.sha256(encoded).hexdigest(),
        "evidence": distribution.evidence(saved),
        "evaluated_at": "2026-10-23T00:00:00+00:00",
    }
    service = SimpleNamespace(
        access=AsyncMock(),
        repo=SimpleNamespace(read=AsyncMock(return_value={"payload": payload})),
        audit=AsyncMock(),
    )
    monkeypatch.setattr(distribution, "report_service", service)
    storage = SimpleNamespace(get=Mock(return_value=encoded))
    monkeypatch.setattr(distribution, "store", lambda: storage)
    monkeypatch.setattr(distribution, "collect", AsyncMock(return_value=saved))
    assert await distribution.activity_snapshot("report-a", "activity", USER) == saved
    with pytest.raises(HTTPException) as denied:
        await distribution.activity_snapshot("report-a", "activity", {**USER, "username": "bob"})
    assert denied.value.status_code == 404 and storage.get.call_count == 1
    changed = snapshot()
    changed["pages"][0]["result"]["widgets"][0]["rows"] = []
    distribution.collect.return_value = changed
    with pytest.raises(HTTPException) as revoked:
        await distribution.activity_snapshot("report-a", "activity", USER)
    assert revoked.value.status_code == 409


async def test_template_rechecks_identity_source_access_and_schema(monkeypatch):
    payload = {
        "kind": "template",
        "identity": distribution.identity(USER),
        "report_id": "source",
        "spec": report_spec().model_dump(mode="json"),
    }
    service = SimpleNamespace(
        repo=SimpleNamespace(read=AsyncMock(return_value={"payload": payload})),
        access=AsyncMock(),
        create=AsyncMock(return_value={"report_id": "new"}),
    )
    monkeypatch.setattr(templates, "report_service", service)
    monkeypatch.setattr(templates, "require_current_session", AsyncMock())
    with pytest.raises(HTTPException):
        await templates.instantiate("template", "operation", {**USER, "active_role": "other"})
    service.create.assert_not_awaited()
    assert await templates.instantiate("template", "operation", USER) == {"report_id": "new"}
    service.access.assert_awaited_once_with("source", USER)
    payload["spec"]["schema_version"] = "99.0.0"
    with pytest.raises(ValueError):
        await templates.instantiate("template", "operation2", USER)


async def test_scheduled_scope_requires_delegated_connection_and_current_binding(monkeypatch):
    from app.modules.query import service
    from app.modules.task_orchestration.repository import task_orchestration_repository

    monkeypatch.setattr(service, "delegated_connection_for", lambda principal: None)
    with pytest.raises(HTTPException), security.scheduled_execution(USER):
        pass
    monkeypatch.setattr(service, "delegated_connection_for", lambda principal: object())
    binding = AsyncMock(return_value=USER["username"])
    monkeypatch.setattr(task_orchestration_repository, "get_role_execution_user", binding)
    with security.scheduled_execution(USER):
        await security.require_current_session(USER)
        binding.return_value = "another-user"
        with pytest.raises(HTTPException):
            await security.require_current_session(USER)
    monkeypatch.setattr(security.session_store, "get", AsyncMock(return_value=None))
    with pytest.raises(HTTPException):
        await security.require_current_session(USER)
