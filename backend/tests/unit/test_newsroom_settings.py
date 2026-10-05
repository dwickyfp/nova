"""The News switch on a Semantic View and the schedule it is allowed to create."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.intelligence import schedules, semantic_views
from app.modules.intelligence.contracts import Scope
from app.modules.intelligence.newsroom_contracts import NewsSettings
from app.modules.task_orchestration import internal_handlers
from app.modules.task_orchestration.credentials import CredentialUnavailable
from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration
from tests.benchmark.news import dataset, warehouse
from tests.unit._newsroom import Newsroom, config

SERVICE_SCOPE = Scope(
    principal=warehouse.SERVICE.username, active_role="news_editor", security_context_version=1
)
ADMIN = {
    "username": "nova_admin",
    "active_role": "ACCOUNTADMIN",
    "roles": ["ACCOUNTADMIN"],
    "assigned_roles": ["ACCOUNTADMIN"],
    "security_context_version": 1,
    "session_id": "session-admin",
}


@pytest.fixture
def room(monkeypatch):
    return Newsroom(monkeypatch)


async def test_enabling_stores_the_configuration_and_schedules_the_bound_account(room):
    view = await room.enable(cadence_minutes=30)

    assert view["news_enabled"] is True
    assert view["news_config"]["execution_user"] == warehouse.SERVICE.username
    assert room.schedules == [
        {
            "view_id": warehouse.VIEW_ID,
            "by": "news_manager",
            "handler": "intelligence.newsroom",
            "enabled": True,
            "cadence": 30,
            "execution_scope": SERVICE_SCOPE,
        }
    ]


async def test_the_execution_account_is_never_taken_from_the_request(room):
    view = await room.enable(execution_user="nova_admin")

    assert view["news_config"]["execution_user"] == warehouse.SERVICE.username
    assert room.schedules[0]["execution_scope"].principal == warehouse.SERVICE.username


async def test_a_caller_who_cannot_manage_the_view_is_told_it_does_not_exist(room):
    with pytest.raises(HTTPException) as refused:
        await room.service.configure(
            warehouse.VIEW_ID,
            NewsSettings(enabled=True, config=config()),
            room.user("news_bandung"),
        )

    assert (refused.value.status_code, refused.value.detail) == (404, "Semantic View not found")
    assert room.schedules == []


async def test_enabling_needs_a_configuration(room):
    with pytest.raises(HTTPException) as refused:
        await room.service.configure(
            warehouse.VIEW_ID, NewsSettings(enabled=True), room.user("news_manager")
        )

    assert refused.value.status_code == 422


@pytest.mark.parametrize(
    "role", ["ACCOUNTADMIN", "accountadmin", "SECURITYADMIN", "root", "public"]
)
async def test_an_administration_role_cannot_publish_news(room, role):
    with pytest.raises(HTTPException) as refused:
        await room.enable(execution_role=role)

    assert refused.value.status_code == 422
    assert refused.value.detail == "Choose a business role, not an administration role"
    assert room.schedules == []


async def test_a_manager_must_hold_the_execution_role(room):
    room.bindings.bindings["finance"] = "nova_task_service_finance"

    with pytest.raises(HTTPException) as refused:
        await room.enable(execution_role="finance")

    assert refused.value.status_code == 403
    assert refused.value.detail == "Activate the role 'finance' before enabling News"
    assert room.warehouse.view["news_enabled"] is False


async def test_an_account_administrator_may_choose_a_bound_business_role(room):
    view = await room.service.configure(
        warehouse.VIEW_ID, NewsSettings(enabled=True, config=config()), ADMIN
    )

    assert view["news_enabled"] is True
    assert room.schedules[0]["execution_scope"] == SERVICE_SCOPE


async def test_a_role_without_an_execution_account_is_refused_with_the_next_step(monkeypatch):
    room = Newsroom(monkeypatch, bindings={})

    with pytest.raises(HTTPException) as refused:
        await room.enable()

    assert refused.value.status_code == 409
    assert refused.value.detail == (
        "Role 'news_editor' has no scheduled execution account. Ask an administrator "
        "to provision one, then enable News again."
    )
    assert room.warehouse.view["news_enabled"] is False


async def test_an_unpublished_view_cannot_enable_news(room):
    room.warehouse.view["active_version"] = None

    with pytest.raises(HTTPException) as refused:
        await room.enable()

    assert (refused.value.status_code, refused.value.detail) == (
        409, "Publish a version before enabling News",
    )


async def test_switching_off_stops_the_schedule_and_keeps_the_configuration(room):
    await room.enable()
    room.schedules.clear()

    view = await room.service.configure(
        warehouse.VIEW_ID, NewsSettings(enabled=False), room.user("news_manager")
    )

    assert view["news_enabled"] is False
    assert view["news_config"]["metrics"] == ["revenue"]
    assert [(row["enabled"], row["execution_scope"]) for row in room.schedules] == [
        (False, SERVICE_SCOPE)
    ]


async def test_any_manager_may_switch_news_off_without_the_execution_role(room):
    await room.enable()

    view = await room.service.configure(warehouse.VIEW_ID, NewsSettings(enabled=False), ADMIN)

    assert view["news_enabled"] is False


async def test_a_changed_execution_account_retires_the_old_schedule_first(room):
    await room.enable()
    room.schedules.clear()
    room.bindings.bindings["news_editor"] = "nova_task_service_news_2"

    await room.enable()

    assert [(row["enabled"], row["execution_scope"].principal) for row in room.schedules] == [
        (False, warehouse.SERVICE.username),
        (True, "nova_task_service_news_2"),
    ]
    assert room.warehouse.view["news_config"]["execution_user"] == "nova_task_service_news_2"


async def test_switch_changes_are_audited(room):
    await room.enable()
    await room.service.configure(
        warehouse.VIEW_ID, NewsSettings(enabled=False), room.user("news_manager")
    )

    assert [
        (call.kwargs["action"], call.kwargs["decision"], call.kwargs["user_name"])
        for call in room.audit.await_args_list
    ] == [("SET_NEWS", "ENABLE", "news_manager"), ("SET_NEWS", "DISABLE", "news_manager")]


async def test_defaults_come_from_the_published_definition(room):
    defaults = await room.service.defaults(warehouse.VIEW_ID, room.user("news_manager"))

    assert defaults["metrics"] == ["revenue"]
    assert defaults["count_metric"] == "order_count"
    assert defaults["slice_dimensions"] == ["city", "channel", "category"]
    assert defaults["time_dimension"] == "sale_date"
    assert defaults["execution_role"] == "news_editor"


def test_only_a_manager_sees_how_news_is_configured():
    view = {
        "id": "v", "owner_name": "news_manager", "news_enabled": True,
        "news_config": {"execution_role": "news_editor"}, "news_updated_by": "news_manager",
        "news_updated_at": "2026-10-05",
    }
    reader = {"username": "news_bandung", "active_role": "news_reader"}

    assert semantic_views._public_view(view, {"username": "news_manager"}) == view
    assert semantic_views._public_view(view, reader) == {
        "id": "v", "owner_name": "news_manager", "news_enabled": True,
    }


def test_a_view_row_normalizes_the_news_columns():
    result = {"columns": ["created_at", "updated_at", "news_enabled", "news_config",
                          "news_updated_at"]}

    assert semantic_views._view_row(result, ["c", "u", None, None, None]) == {
        "created_at": "c", "updated_at": "u", "news_enabled": False, "news_config": None,
        "news_updated_at": None,
    }
    assert semantic_views._view_row(result, ["c", "u", 1, '{"metrics": ["revenue"]}', "t"])[
        "news_config"
    ] == {"metrics": ["revenue"]}


class Tasks:
    def __init__(self, bound):
        self.bound = bound
        self.tasks: dict[str, dict] = {}

    async def get_role_execution_user(self, role):
        return self.bound

    async def find_task(self, name, database, schema):
        return self.tasks.get(name)

    async def create_task(self, data, created_by):
        task = {**data, "id": f"task-{len(self.tasks)}", "created_by": created_by}
        self.tasks[data["name"]] = task
        return task

    async def update_task(self, task_id, data):
        task = next(row for row in self.tasks.values() if row["id"] == task_id)
        task.update(data)
        return task


@pytest.fixture
def tasks(monkeypatch):
    @asynccontextmanager
    async def lock(_key):
        yield

    fake = Tasks(warehouse.SERVICE.username)
    monkeypatch.setattr(schedules, "task_orchestration_repository", fake)
    monkeypatch.setattr(schedules, "metadata_lock", lock)
    return fake


async def _schedule(user, *, enabled=True, scope=SERVICE_SCOPE, cadence=60):
    return await schedules.configure_schedule(
        SimpleNamespace(id=warehouse.VIEW_ID),
        user,
        handler="intelligence.newsroom",
        enabled=enabled,
        cadence=cadence,
        execution_scope=scope,
    )


async def test_the_schedule_belongs_to_the_bound_account_not_the_manager(tasks):
    manager = warehouse.PRINCIPALS["news_manager"].user()

    result = await _schedule(manager)

    [task] = tasks.tasks.values()
    assert result == {"task_id": task["id"], "enabled": True}
    assert (task["created_by"], task["owner_role"]) == (warehouse.SERVICE.username, "news_editor")
    assert (task["schedule_kind"], task["schedule_expr"]) == ("interval", "60 minutes")
    assert task["handler"] == "intelligence.newsroom"
    assert task["handler_config"] == InternalTaskConfiguration(
        scope=SERVICE_SCOPE, record_id=warehouse.VIEW_ID
    ).model_dump(mode="json")


async def test_a_schedule_is_refused_when_the_binding_no_longer_matches(tasks):
    tasks.bound = "nova_task_service_other"

    with pytest.raises(HTTPException) as refused:
        await _schedule(warehouse.PRINCIPALS["news_manager"].user())

    assert refused.value.status_code == 409
    assert tasks.tasks == {}


async def test_disabling_turns_the_schedule_manual_even_after_a_rebinding(tasks):
    manager = warehouse.PRINCIPALS["news_manager"].user()
    await _schedule(manager)
    tasks.bound = "nova_task_service_other"

    result = await _schedule(manager, enabled=False)

    [task] = tasks.tasks.values()
    assert result["enabled"] is False
    assert (task["schedule_kind"], task["schedule_expr"]) == ("manual", None)


async def test_without_an_execution_scope_the_caller_must_still_be_the_bound_account(tasks):
    with pytest.raises(HTTPException) as refused:
        await schedules.configure_schedule(
            SimpleNamespace(id="monitor"),
            warehouse.PRINCIPALS["news_manager"].user(),
            handler="intelligence.monitor",
            enabled=True,
        )

    assert refused.value.status_code == 403


async def test_the_newsroom_handler_is_allowlisted_and_runs_the_cycle(monkeypatch):
    from app.modules.intelligence import newsroom

    calls = []

    async def run_cycle(view_id, user, *, now=None):
        calls.append((view_id, user["username"]))
        return {"status": "pressed"}

    monkeypatch.setattr(newsroom.newsroom_service, "run_cycle", run_cycle)
    service = warehouse.SERVICE.user()

    result = await internal_handlers.run_once(
        "intelligence.newsroom",
        InternalTaskConfiguration(scope=SERVICE_SCOPE, record_id=warehouse.VIEW_ID),
        service,
    )

    assert "intelligence.newsroom" in internal_handlers.HANDLERS
    assert result == {"status": "pressed"}
    assert calls == [(warehouse.VIEW_ID, warehouse.SERVICE.username)]


async def test_the_newsroom_handler_refuses_another_identity():
    with pytest.raises(CredentialUnavailable):
        await internal_handlers.run_once(
            "intelligence.newsroom",
            InternalTaskConfiguration(scope=SERVICE_SCOPE, record_id=warehouse.VIEW_ID),
            warehouse.PRINCIPALS["news_manager"].user(),
        )


def test_the_dataset_marks_every_edition_day():
    assert len(dataset.EDITION_DAYS) == 7
    assert dataset.EDITION_DAYS[-1] == dataset.LAST_DAY
