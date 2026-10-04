"""New requests use deployment calendars; stored objects and replay retain theirs."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.modules.agents import router as agent_router
from app.modules.intelligence import actions, engine_router, schedules
from app.modules.intelligence.action_contracts import ActionPreview, AutomationActionConfiguration
from app.modules.intelligence.contracts import Monitor, MonitorConfiguration, Scope, fingerprint
from app.modules.intelligence.engine import IntelligenceService
from tests.unit.test_business_actions import FakeAdapter
from tests.unit.test_business_actions import action_program as action_program
from tests.unit.test_business_actions import program as program
from tests.unit.test_intelligence_engine import REF, USER, MemoryRepository, ObservationSource

ZONES = ("Asia/Jakarta", "UTC", "Europe/Berlin", "America/New_York")
ADAPTERS = ("monitor-v1", "automation-v1")
MONITOR_INPUT = {
    "name": "Revenue", "agent_id": "finance", "semantic": REF.model_dump(mode="json"),
    "plan": {"metrics": ["revenue", "orders"]}, "value_column": "revenue",
    "count_column": "orders", "time_dimension": "ordered_at", "enabled": True,
}
AUTOMATION_INPUT = {
    "agent_id": "finance", "semantic": REF.model_dump(mode="json"), "title": "Revenue",
    "prompt": "Revenue today", "schedule_kind": "cron", "schedule_expr": "0 8 * * *",
}


@pytest.fixture
def monitor_api(monkeypatch):
    repo = MemoryRepository()
    service = IntelligenceService(repo, ObservationSource())
    monkeypatch.setattr(service, "validate_monitor", AsyncMock())
    monkeypatch.setattr(service, "authorize_record", AsyncMock())
    monkeypatch.setattr(service, "_audit", AsyncMock())
    monkeypatch.setattr(engine_router, "intelligence_service", service)
    admission, binding, schedule = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(agent_router, "_require_agent", admission)
    monkeypatch.setattr(schedules, "require_execution_binding", binding)
    monkeypatch.setattr(schedules, "configure_schedule", schedule)
    return SimpleNamespace(service=service, repo=repo, admission=admission,
                           binding=binding, schedule=schedule)


def monitor_request(**configuration):
    return engine_router.MonitorCreate.model_validate({
        "operation_id": "monitor-timezone", "configuration": {**MONITOR_INPUT, **configuration},
    })


@pytest.mark.parametrize("zone", ZONES)
async def test_monitor_creation_resolves_omitted_timezone_at_request_time(
    monitor_api, monkeypatch, zone,
):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", f" {zone} ")
    request = monitor_request()
    before = request.model_dump(mode="json")
    saved = await engine_router.create_monitor(request, USER)
    assert saved.timezone == zone and saved.scope == Scope.from_user(USER)
    assert request.model_dump(mode="json") == before
    assert "timezone" not in request.configuration.model_fields_set
    monitor_api.admission.assert_awaited_once_with("finance", USER)
    monitor_api.binding.assert_awaited_once_with(USER)
    monitor_api.service.validate_monitor.assert_awaited_once_with(saved, USER)
    monitor_api.schedule.assert_awaited_once_with(
        saved, USER, handler="intelligence.monitor", enabled=True, cadence=15,
    )


@pytest.mark.parametrize("zone", ZONES)
async def test_monitor_creation_preserves_explicit_timezone(monitor_api, monkeypatch, zone):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC" if zone != "UTC" else "Europe/Berlin")
    saved = await engine_router.create_monitor(monitor_request(timezone=zone), USER)
    assert saved.timezone == zone


@pytest.mark.parametrize("zone", ("Asia/Jakarta", "UTC"))
async def test_monitor_omitted_timezone_retry_survives_configuration_change(
    monitor_api, monkeypatch, zone,
):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", zone)
    saved = await engine_router.create_monitor(monitor_request(), USER)
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "Europe/Berlin")
    replay = await engine_router.create_monitor(monitor_request(), USER)
    assert replay == saved and replay.revision == 1
    assert len(monitor_api.repo.rows) == 1


@pytest.mark.parametrize("change", ({"timezone": "Europe/Berlin"}, {"name": "Other"}))
async def test_monitor_retry_still_rejects_operation_collision(monitor_api, monkeypatch, change):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    saved = await engine_router.create_monitor(monitor_request(), USER)
    with pytest.raises(HTTPException) as error:
        await engine_router.create_monitor(monitor_request(**change), USER)
    assert error.value.status_code == 409
    assert monitor_api.repo.rows[("monitors", saved.id)] == saved
    assert monitor_api.schedule.await_count == 1


@pytest.mark.parametrize("override", (None, "UTC"))
async def test_monitor_update_preserves_historical_timezone_unless_explicit(
    monitor_api, monkeypatch, override,
):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    stored = Monitor(id="legacy-monitor", scope=Scope.from_user(USER), **MONITOR_INPUT)
    monitor_api.repo.rows[("monitors", stored.id)] = stored
    config = {**MONITOR_INPUT, "name": "Updated revenue"}
    if override is not None:
        config["timezone"] = override
    request = engine_router.MonitorUpdate.model_validate({
        "expected_revision": stored.revision, "configuration": config,
    })
    saved = await engine_router.update_monitor(stored.id, request, USER)
    assert saved.timezone == (override or "Asia/Jakarta") and saved.revision == 2
    assert stored.timezone == "Asia/Jakarta" and saved.scope == stored.scope
    monitor_api.service.authorize_record.assert_awaited_once()


@pytest.mark.parametrize("guard", ("admission", "binding"))
async def test_monitor_timezone_resolution_retains_admission_guards(monitor_api, guard):
    getattr(monitor_api, guard).side_effect = HTTPException(status_code=403, detail="Denied")
    with pytest.raises(HTTPException) as error:
        await engine_router.create_monitor(monitor_request(), USER)
    assert error.value.status_code == 403
    assert monitor_api.repo.rows == {}
    monitor_api.schedule.assert_not_awaited()
    monitor_api.service.validate_monitor.assert_not_awaited()


def action_request(action_program, adapter, **configuration):
    service, _, original, *_ = action_program
    service.adapters.setdefault(adapter, FakeAdapter())
    payload = original.model_dump(mode="json")
    payload.update(idempotency_key=f"request-timezone-{adapter}", adapter_id=adapter)
    payload["configuration"] = {
        **(MONITOR_INPUT if adapter == "monitor-v1" else AUTOMATION_INPUT), **configuration,
    }
    return ActionPreview.model_validate(payload)


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("zone", ZONES)
async def test_action_creation_resolves_omitted_timezone_before_digest(
    action_program, monkeypatch, adapter, zone,
):
    service, *_ = action_program
    request = action_request(action_program, adapter)
    before = request.model_dump(mode="json")
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", f" {zone} ")
    spy = AsyncMock(wraps=service.adapters[adapter].preview)
    monkeypatch.setattr(service.adapters[adapter], "preview", spy)
    saved = await service.preview(request, USER)
    expected = deepcopy(before)
    expected["configuration"]["timezone"] = zone
    assert saved.configuration.timezone == zone
    assert saved.request_digest == fingerprint(expected)
    assert saved.scope == Scope.from_user(USER)
    assert spy.await_args.args == (saved.configuration, USER)
    assert request.model_dump(mode="json") == before
    assert "timezone" not in request.configuration.model_fields_set


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("zone", ZONES)
async def test_action_creation_preserves_explicit_timezone(
    action_program, monkeypatch, adapter, zone,
):
    service, *_ = action_program
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC" if zone != "UTC" else "Europe/Berlin")
    request = action_request(action_program, adapter, timezone=zone)
    saved = await service.preview(request, USER)
    assert saved.configuration.timezone == zone
    assert saved.request_digest == fingerprint(request.model_dump(mode="json"))


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_action_omitted_timezone_retry_survives_configuration_change(
    action_program, monkeypatch, adapter,
):
    service, *_ = action_program
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    saved = await service.preview(action_request(action_program, adapter), USER)
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "Europe/Berlin")
    replay = await service.preview(action_request(action_program, adapter), USER)
    assert replay == saved and replay.configuration.timezone == "UTC"


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_action_omitted_timezone_retry_matches_historical_jakarta_digest(
    action_program, monkeypatch, adapter,
):
    service, *_ = action_program
    omitted = action_request(action_program, adapter)
    legacy_digest = fingerprint(omitted.model_dump(mode="json"))
    saved = await service.preview(action_request(action_program, adapter, timezone="Asia/Jakarta"),
                                  USER)
    assert saved.request_digest == legacy_digest
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    assert await service.preview(omitted, USER) == saved


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("change", ("timezone", "business_input"))
async def test_action_omitted_timezone_retry_keeps_collision_checks(
    action_program, monkeypatch, adapter, change,
):
    service, *_ = action_program
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    saved = await service.preview(action_request(action_program, adapter), USER)
    patch = {"timezone": "Europe/Berlin"} if change == "timezone" else {
        "name" if adapter == "monitor-v1" else "title": "Other",
    }
    with pytest.raises(HTTPException) as error:
        await service.preview(action_request(action_program, adapter, **patch), USER)
    assert error.value.status_code == 409
    assert error.value.detail == "Idempotency key inputs changed"
    assert await service.get(saved.id, USER) == saved


@pytest.mark.parametrize("adapter", ADAPTERS)
async def test_action_omitted_timezone_retry_revalidates_current_session(
    action_program, monkeypatch, adapter,
):
    service, *_ = action_program
    saved = await service.preview(action_request(action_program, adapter), USER)
    monkeypatch.setattr(actions.session_store, "get", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as error:
        await service.preview(action_request(action_program, adapter), USER)
    assert error.value.status_code == 403 and saved.scope == Scope.from_user(USER)


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize("collision", ("omitted", "explicit", "changed"))
async def test_action_publication_fence_restores_competing_record_timezone_only_for_omission(
    action_program, monkeypatch, adapter, collision,
):
    service, existing, _, _, lease, _, repo, _ = action_program
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", "UTC")
    request = action_request(action_program, adapter, **({"timezone": "UTC"}
                                                       if collision == "explicit" else {}))
    payload = request.model_dump(mode="json")
    payload["configuration"]["timezone"] = "Asia/Jakarta"
    if collision == "changed":
        payload["configuration"]["name" if adapter == "monitor-v1" else "title"] = "Other"
    competing = existing.model_copy(update={
        "id": fingerprint([Scope.from_user(USER).model_dump(exclude={"session_id"}),
                           request.idempotency_key]),
        "idempotency_key": request.idempotency_key,
        "adapter_id": adapter,
        "action_type": "monitor" if adapter == "monitor-v1" else "automation",
        "configuration": type(request.configuration).model_validate(payload["configuration"]),
        "request_digest": fingerprint(payload),
        "receipt": None,
    })

    @asynccontextmanager
    async def publish_competitor(_key):
        repo.rows[("actions", competing.id)] = deepcopy(competing)
        yield lease

    monkeypatch.setattr(actions, "metadata_lock", publish_competitor)
    if collision == "omitted":
        assert await service.preview(request, USER) == competing
    else:
        with pytest.raises(HTTPException) as error:
            await service.preview(request, USER)
        assert error.value.status_code == 409
        assert error.value.detail == "Idempotency key inputs changed"
    assert repo.rows[("actions", competing.id)] == competing
    assert lease.renews == 0


@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("adapter,model,payload,configuration_digest,preview_digest", [
    ("monitor-v1", MonitorConfiguration, MONITOR_INPUT,
     "b6999d23119615dada138be7430f3125094eb1b20b01cab255873d3f2dc2403a",
     "9d9bad20b2e0e66096ec241bf8f3c617ab3a19c0f88148d234014332e9453fc2"),
    ("automation-v1", AutomationActionConfiguration, AUTOMATION_INPUT,
     "bb2675b91db3a22bc1ec908fae203296cfbc4cc7a6f337091ef73ef321116231",
     "3f2be01310233a467b18e4e0a6aa11b1d4030fd8f956aef9fe4c3a429558e623"),
])
def test_historical_configuration_and_preview_digests_are_configuration_independent(
    monkeypatch, zone, adapter, model, payload, configuration_digest, preview_digest,
):
    monkeypatch.setattr(settings, "NOVA_TIMEZONE", zone)
    configuration = model.model_validate(payload)
    assert configuration.timezone == "Asia/Jakarta"
    assert fingerprint(configuration.model_dump(mode="json")) == configuration_digest
    preview = ActionPreview(idempotency_key="historical-timezone", decision_id="decision",
                            expected_decision_revision=1, option_id="chosen", adapter_id=adapter,
                            configuration=configuration)
    assert fingerprint(preview.model_dump(mode="json")) == preview_digest
