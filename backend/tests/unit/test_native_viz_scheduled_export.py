"""Scheduled dashboard exports: the same queries as the editor, a valid workbook,
and schedules that only run as a business role's scheduled-task account."""

import io
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4
from xml.etree import ElementTree

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.exceptions import NovaException, StorageError
from app.modules.task_orchestration.internal_handlers import HANDLERS
from app.modules.viz import export_schedules as module
from app.modules.viz.canvas import dashboard_definition
from app.modules.viz.contracts.charts import ChartDefinition
from app.modules.viz.contracts.dashboards import DashboardDefinition
from app.modules.viz.contracts.queries import DatasetQueryResult
from app.modules.viz.export_schedules import ScheduleRequest, describe
from app.modules.viz.scheduled_export import (
    ExportUnavailable,
    cell_value,
    chart_request,
    default_context,
    file_name,
    parse_record,
    query_shape,
    record_for,
    result_sheet,
    sheet_names,
    workbook,
)

DATASET = UUID("00000000-0000-4000-8000-000000000006")
OTHER = UUID("00000000-0000-4000-8000-000000000007")
DASHBOARD = UUID("00000000-0000-4000-8000-000000000001")
PAGE = UUID("00000000-0000-4000-8000-000000000002")
WIDGET = UUID("00000000-0000-4000-8000-000000000003")
CHART = UUID("00000000-0000-4000-8000-000000000005")
PAGE_2 = UUID("00000000-0000-4000-8000-000000000008")


def chart(encoding: dict, visual: str = "bar", **extra) -> ChartDefinition:
    return ChartDefinition.model_validate({
        "chart_id": str(CHART), "title": "Revenue", "dataset_id": str(DATASET),
        "dataset_revision": 1, "visual": visual, "encoding": encoding, **extra,
    })


def binding(field: str = "region", dataset: UUID = DATASET) -> dict:
    return {"dataset_id": str(dataset), "dataset_revision": 1, "field_id": field}


def board(filters: list | None = None) -> DashboardDefinition:
    return dashboard_definition({
        "dashboard_id": str(DASHBOARD), "title": "Sales", "filters": filters or [],
        "pages": [{"page_id": str(PAGE), "title": "Overview", "widgets": [{
            "kind": "chart", "widget_id": str(WIDGET), "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
            "chart_ref": {"chart_id": str(CHART), "revision": 1}}]},
            {"page_id": str(PAGE_2), "title": "Detail"}],
    })


# ── Query shape: the same cases as the editor's chart-query.ts ─────────────

def test_bar_with_a_split_and_sort_by_measure():
    shape = query_shape(chart({
        "kind": "cartesian", "dimensions": [{"field_id": "month", "date_grain": "month"}],
        "measures": [{"field_id": "revenue", "aggregation": "sum"}],
        "color": {"field_id": "channel"}},
        sort=[{"field_id": "revenue", "direction": "desc", "aggregation": "sum"}], row_limit=50))
    assert shape == {
        "dimensions": [{"field_id": "month", "date_grain": "month"}, {"field_id": "channel"}],
        "measures": [{"field_id": "revenue", "aggregation": "sum"}],
        "sort": [{"column": "m0", "direction": "desc"}], "limit": 50,
    }


def test_split_on_an_axis_field_is_not_repeated():
    shape = query_shape(chart({
        "kind": "cartesian", "dimensions": [{"field_id": "region"}],
        "measures": [{"field_id": "revenue", "aggregation": "sum"}],
        "color": {"field_id": "region"}}))
    assert [item["field_id"] for item in shape["dimensions"]] == ["region"]


def test_category_sorts_by_value_and_caps_its_slices():
    shape = query_shape(chart({
        "kind": "category", "category": {"field_id": "region"},
        "measure": {"field_id": "revenue", "aggregation": "sum"}, "max_categories": 5}, "pie"))
    assert shape["sort"] == [{"column": "m0", "direction": "desc"}] and shape["limit"] == 5


def test_map_without_a_value_counts_rows():
    shape = query_shape(chart({"kind": "map", "latitude": "lat", "longitude": "lng",
                               "group": {"field_id": "country"}}, "map"))
    assert [item["field_id"] for item in shape["dimensions"]] == ["lat", "lng", "country"]
    assert shape["measures"] == [{"field_id": "lat", "aggregation": "count"}]


def test_pivot_groups_by_rows_then_columns():
    shape = query_shape(chart({
        "kind": "table", "dimensions": [{"field_id": "region"}],
        "pivot_columns": [{"field_id": "channel"}],
        "measures": [{"field_id": "units", "aggregation": "sum"}]}, "pivot"))
    assert [item["field_id"] for item in shape["dimensions"]] == ["region", "channel"]


def test_an_unsupported_aggregation_is_refused():
    with pytest.raises(ExportUnavailable):
        query_shape(chart({"kind": "single_measure",
                           "measure": {"field_id": "revenue", "aggregation": "median"}}, "kpi"))


# ── Dashboard filter defaults ───────────────────────────────────────────────

def members(values: list, scope: dict | None = None, field: str = "region") -> dict:
    return {"filter_id": str(uuid4()), "title": "Region", "kind": "multi_value",
            "field": binding(field), "scope": scope or {"kind": "global"},
            "defaults": [{"type": "string", "value": value} for value in values]}


def test_default_members_become_one_predicate():
    context = default_context(board([members(["Jakarta", "Bandung"])]), chart({
        "kind": "cartesian", "dimensions": [{"field_id": "region"}],
        "measures": [{"field_id": "revenue", "aggregation": "sum"}]}), WIDGET, PAGE, "UTC")
    assert context and context["op"] == "in" and len(context["values"]) == 2


def test_filters_without_defaults_other_datasets_or_pages_do_not_apply():
    other_dataset = {**members(["Jakarta"]), "field": binding(dataset=OTHER)}
    other_page = members(["Jakarta"], {"kind": "page", "page_id": str(PAGE_2)})
    no_default = members([])
    target = chart({"kind": "cartesian", "dimensions": [{"field_id": "region"}],
                    "measures": [{"field_id": "revenue", "aggregation": "sum"}]})
    assert default_context(board([other_dataset, other_page, no_default]), target,
                           WIDGET, PAGE, "UTC") is None


def test_a_date_range_includes_its_whole_last_day_and_relative_dates_use_the_schedule_zone():
    date_range = {"filter_id": str(uuid4()), "title": "Dates", "kind": "date_range",
                  "field": binding("order_date"), "scope": {"kind": "global"},
                  "defaults": [{"type": "date", "value": "2026-01-01"},
                               {"type": "date", "value": "2026-01-31"}]}
    recent = {"filter_id": str(uuid4()), "title": "Recent", "kind": "relative_date",
              "field": binding("order_date"), "scope": {"kind": "global"},
              "relative": {"unit": "month", "count": 3, "mode": "previous"}}
    target = chart({"kind": "cartesian", "dimensions": [{"field_id": "region"}],
                    "measures": [{"field_id": "revenue", "aggregation": "sum"}]},
                   filters={"expression": {"type": "predicate", "field": binding(), "op": "eq",
                                           "values": [{"type": "string", "value": "Jakarta"}]}})
    request = chart_request(board([date_range, recent]), target, WIDGET, PAGE, "Asia/Jakarta")
    tree = request.model_dump(mode="json")["filters"]["expression"]
    assert tree["type"] == "and" and tree["children"][0]["op"] == "eq"
    ranged, relative = tree["children"][1]["children"]
    assert ranged["children"][1]["op"] == "lt"
    assert ranged["children"][1]["values"][0]["value"] == "2026-02-01"
    assert relative["relative"]["timezone"] == "Asia/Jakarta"
    assert request.draft is False


# ── Workbook ────────────────────────────────────────────────────────────────

def result(rows: list, date_grain: str | None = "month") -> DatasetQueryResult:
    return DatasetQueryResult.model_validate({
        "dataset_id": str(DATASET), "revision": 1, "truncated": False,
        "columns": [
            {"key": "d0", "name": "order_date", "logical_type": "date", "role": "dimension",
             "field_id": "order_date", "date_grain": date_grain},
            {"key": "m0", "name": "revenue", "logical_type": "decimal", "role": "measure",
             "field_id": "revenue", "aggregation": "sum"},
        ],
        "rows": rows,
    })


def test_cells_keep_numbers_as_numbers_and_dates_as_their_grain():
    sheet = result_sheet("Revenue", result([
        [{"type": "date", "value": "2026-01-01"}, {"type": "decimal", "value": "1250.50"}],
        [{"type": "date", "value": "2026-04-01"}, None],
    ]))
    assert sheet == ("Revenue", ["Order date", "Sum of revenue"],
                     [["Jan 2026", 1250.5], ["Apr 2026", None]])


def test_numbers_a_spreadsheet_would_round_stay_text():
    column = result([]).columns[1]
    assert cell_value(SimpleNamespace(type="integer", value="1234567890123456789"),
                      column) == "1234567890123456789"


def test_sheet_names_are_valid_and_unique():
    assert sheet_names(["Revenue: by/region?", "Revenue  by region", "x" * 40, ""]) == [
        "Revenue  by region", "Revenue  by region (2)", "x" * 31, "Chart"]


def test_workbook_is_well_formed_and_formula_text_is_inert():
    data = workbook([("Revenue", ["Region", "Amount"], [["=HYPERLINK(1)", 10.0], ["<b>", True]]),
                     ("Units", ["Region"], [["Bandung"]])])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "xl/workbook.xml", "xl/worksheets/sheet2.xml"} <= names
        for name in names:
            ElementTree.fromstring(archive.read(name))
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "'=HYPERLINK(1)" in sheet and "&lt;b&gt;" in sheet and '<v>10.0</v>' in sheet
    assert '<c r="B3" t="b"><v>1</v></c>' in sheet


def test_an_empty_page_has_no_workbook():
    with pytest.raises(ExportUnavailable):
        workbook([])


def test_file_name_is_safe_and_dated():
    when = datetime(2026, 11, 16, 7, 5, tzinfo=UTC)
    assert file_name("Sales / Q4: final", "Overview", when) == (
        "Sales Q4 final Overview 2026-11-16 0705.xlsx")


def test_records_carry_the_time_zone_and_refuse_unknown_ones():
    record = record_for(DASHBOARD, PAGE, "Asia/Jakarta")
    target, zone = parse_record(record)
    assert (target.dashboard_id, target.page_id, zone) == (DASHBOARD, PAGE, "Asia/Jakarta")
    assert len(record) <= 128
    with pytest.raises(ExportUnavailable):
        parse_record(f"{DASHBOARD}:{PAGE}:xlsx:Mars/Base")


def test_the_handler_is_allowlisted():
    assert "viz.dashboard_export" in HANDLERS


# ── Schedules ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body, cron", [
    ({"cadence": "daily", "time": "07:30"}, "30 7 * * *"),
    ({"cadence": "weekly", "time": "08:00", "weekday": 0}, "0 8 * * 1"),
    ({"cadence": "weekly", "time": "08:00", "weekday": 6}, "0 8 * * 0"),
    ({"cadence": "monthly", "time": "23:59", "day": 28}, "59 23 28 * *"),
])
def test_cadence_becomes_cron_and_back(body, cron):
    request = ScheduleRequest(page_id=PAGE, **body)
    assert request.cron() == cron
    assert describe(cron) == {key: value for key, value in body.items()}


@pytest.mark.parametrize("body", [
    {"cadence": "weekly", "time": "08:00"},
    {"cadence": "daily", "time": "08:00", "day": 3},
    {"cadence": "monthly", "time": "08:00", "day": 31},
    {"cadence": "daily", "time": "24:00"},
    {"cadence": "daily", "time": "08:00", "timezone": "Mars/Base"},
    {"cadence": "daily", "time": "08:00", "timezone": "UTC:evil"},
])
def test_invalid_schedules_are_refused(body):
    with pytest.raises(ValidationError):
        ScheduleRequest(page_id=PAGE, **body)


def person(role: str = "marketing") -> dict:
    return {"username": "alice", "active_role": role, "session_id": "alice-login",
            "assigned_roles": [role], "security_context_version": 1}


@pytest.fixture
def wired(monkeypatch):
    from app.modules.intelligence import schedules as intelligence_schedules
    from app.modules.task_orchestration import repository as task_repository
    from app.modules.viz import document_service
    from app.modules.workspaces import spaces

    documents = SimpleNamespace(
        require_kind=AsyncMock(),
        permissions=AsyncMock(return_value=["view", "export", "edit"]),
        scheduled_permissions=AsyncMock(return_value={"view", "export"}),
        read=AsyncMock(return_value={"definition": board()}),
        workspace_of=AsyncMock(return_value="0123456789abcdef0123456789abcdef"),
    )
    tasks = SimpleNamespace(get_role_execution_user=AsyncMock(return_value="nova_task_service_mkt"),
                            list_tasks=AsyncMock(return_value=[]), delete_task=AsyncMock())
    configure = AsyncMock(return_value={"task_id": "task-1", "enabled": True})
    resolve = AsyncMock()
    monkeypatch.setattr(document_service, "native_document_service", documents)
    monkeypatch.setattr(task_repository, "task_orchestration_repository", tasks)
    monkeypatch.setattr(intelligence_schedules, "configure_schedule", configure)
    monkeypatch.setattr(spaces.space_service, "resolve", resolve)
    monkeypatch.setattr(module, "write_audit_log", AsyncMock())
    return SimpleNamespace(documents=documents, tasks=tasks, configure=configure, resolve=resolve)


async def test_a_schedule_runs_as_the_roles_account_in_its_time_zone(wired):
    saved = await module.native_export_schedules.save(person(), DASHBOARD, ScheduleRequest(
        page_id=PAGE, cadence="weekly", time="07:00", weekday=0, timezone="Asia/Jakarta"))
    assert saved["schedule_id"] == "task-1" and saved["cadence"] == "weekly"
    kwargs = wired.configure.await_args.kwargs
    assert kwargs["handler"] == "viz.dashboard_export"
    assert kwargs["execution_scope"].principal == "nova_task_service_mkt"
    assert kwargs["execution_scope"].active_role == "marketing"
    assert (kwargs["schedule_kind"], kwargs["schedule_expr"], kwargs["timezone"]) == (
        "cron", "0 7 * * 1", "Asia/Jakarta")
    # The account it runs as is checked for view+export and workspace edit.
    wired.documents.scheduled_permissions.assert_awaited_once_with(
        "nova_task_service_mkt", "marketing", DASHBOARD)
    runner = wired.resolve.await_args.args[0]
    assert runner["username"] == "nova_task_service_mkt" and wired.resolve.await_args.kwargs == {
        "edit": True}


@pytest.mark.parametrize("change, status, words", [
    ("no_export", 403, "does not include downloading"),
    ("admin_role", 422, "business role"),
    ("no_account", 409, "no scheduled-task account"),
    ("unpublished", 409, "Publish the dashboard first"),
    ("personal", 409, "personal workspace"),
    ("runner_cannot_export", 409, "including Export"),
    ("runner_cannot_edit", 409, "with edit access"),
])
async def test_schedules_are_refused_with_a_reason(wired, change, status, words):
    user = person()
    if change == "no_export":
        wired.documents.permissions.return_value = ["view"]
    elif change == "admin_role":
        user = person("ACCOUNTADMIN")
    elif change == "no_account":
        wired.tasks.get_role_execution_user.return_value = None
    elif change == "unpublished":
        wired.documents.read.side_effect = NovaException("Document not found", status_code=404)
    elif change == "personal":
        wired.documents.workspace_of.return_value = "personal"
    elif change == "runner_cannot_export":
        wired.documents.scheduled_permissions.return_value = {"view"}
    elif change == "runner_cannot_edit":
        wired.resolve.side_effect = StorageError("view only", status_code=403)
    with pytest.raises(HTTPException) as refused:
        await module.native_export_schedules.save(user, DASHBOARD, ScheduleRequest(
            page_id=PAGE, cadence="daily", time="07:00"))
    assert refused.value.status_code == status and words in refused.value.detail
    wired.configure.assert_not_awaited()


async def test_a_new_time_zone_replaces_the_roles_schedule_for_that_page(wired):
    wired.tasks.list_tasks.return_value = [{
        "id": "old", "handler": "viz.dashboard_export", "owner_role": "marketing",
        "handler_config": {"record_id": record_for(DASHBOARD, PAGE, "UTC")},
    }]
    await module.native_export_schedules.save(person(), DASHBOARD, ScheduleRequest(
        page_id=PAGE, cadence="daily", time="07:00", timezone="Asia/Jakarta"))
    wired.tasks.delete_task.assert_awaited_once_with("old")


async def test_listing_shows_this_dashboards_schedules_only(wired):
    wired.tasks.list_tasks.return_value = [
        {"id": "a", "handler": "viz.dashboard_export", "owner_role": "marketing",
         "schedule_kind": "cron", "schedule_expr": "0 7 * * 1",
         "handler_config": {"record_id": record_for(DASHBOARD, PAGE, "Asia/Jakarta")}},
        {"id": "b", "handler": "viz.dashboard_export", "owner_role": "finance",
         "schedule_kind": "cron", "schedule_expr": "0 6 1 * *",
         "handler_config": {"record_id": record_for(uuid4(), PAGE, "UTC")}},
        {"id": "c", "handler": "intelligence.monitor", "handler_config": {"record_id": "x"}},
    ]
    listed = await module.native_export_schedules.list(person(), DASHBOARD)
    assert [(item["schedule_id"], item["cadence"], item["weekday"], item["mine"])
            for item in listed] == [("a", "weekly", 0, True)]


async def test_only_the_same_role_removes_a_schedule(wired):
    wired.tasks.list_tasks.return_value = [{
        "id": "a", "handler": "viz.dashboard_export", "owner_role": "finance",
        "handler_config": {"record_id": record_for(DASHBOARD, PAGE, "UTC")}}]
    with pytest.raises(HTTPException) as refused:
        await module.native_export_schedules.remove(person(), DASHBOARD, uuid4())
    assert refused.value.status_code == 404
    wired.tasks.list_tasks.return_value[0]["id"] = str(task := uuid4())
    with pytest.raises(HTTPException):
        await module.native_export_schedules.remove(person(), DASHBOARD, task)
    wired.tasks.delete_task.assert_not_awaited()


# ── Scheduled runs have no sign-in session ─────────────────────────────────

def _expected(session_id: str):
    from app.modules.access_control.security_context import SecurityContext

    return SecurityContext(principal="nova_task_service_mkt", active_role="marketing",
                           session_id=session_id)


def test_a_task_session_is_accepted_only_inside_the_matching_delegation():
    from app.modules.query.service import delegated_connection
    from app.modules.viz.document_service import _scheduled_session

    assert _scheduled_session(_expected("task:run-1")) is None
    with delegated_connection("someone_else", object()):
        assert _scheduled_session(_expected("task:run-1")) is None
    with delegated_connection("nova_task_service_mkt", object()):
        session = _scheduled_session(_expected("task:run-1"))
        assert session == {"username": "nova_task_service_mkt", "session_id": "task:run-1",
                           "active_role": "marketing", "assigned_roles": ["marketing"],
                           "security_context_version": 1}
        # A sign-in session id never skips the live-session check.
        assert _scheduled_session(_expected("alice-login")) is None


async def test_current_session_still_refuses_a_task_session_outside_the_worker(monkeypatch):
    from app.core.exceptions import SessionExpiredError
    from app.modules.viz import document_service

    monkeypatch.setattr(document_service.session_store, "get", AsyncMock(return_value=None))
    with pytest.raises(SessionExpiredError):
        await document_service.current_session({
            "username": "nova_task_service_mkt", "active_role": "marketing",
            "assigned_roles": ["marketing"], "session_id": "task:run-1",
            "security_context_version": 1})


# ── Query layer: only the worker's delegated session replaces a managed one ──

@pytest.mark.parametrize("bound, dataset", [(True, False), (False, True)])
async def test_a_client_connection_never_runs_private_dataset_or_bound_queries(bound, dataset):
    from app.core.exceptions import StarRocksError
    from app.modules.query.provenance import DatasetQuery, dataset_query
    from app.modules.query.repository import QueryRepository

    client_socket = object()
    parameters = object() if bound else None
    with pytest.raises(StarRocksError, match="managed session"):
        if dataset:
            with dataset_query(DatasetQuery(dataset_id=uuid4(), operation="query")):
                await QueryRepository().execute_as_user(
                    "SELECT 1", "alice", "", role="marketing", connected=client_socket)
        else:
            await QueryRepository().execute_as_user(
                "SELECT 1", "alice", "", role="marketing", connected=client_socket,
                parameters=parameters)


def test_only_the_matching_delegated_connection_counts_as_a_worker_session():
    from app.modules.query.repository import _worker_session
    from app.modules.query.service import delegated_connection

    worker = object()
    assert not _worker_session(worker, "nova_task_service_mkt")
    with delegated_connection("nova_task_service_mkt", worker):
        assert _worker_session(worker, "nova_task_service_mkt")
        assert not _worker_session(object(), "nova_task_service_mkt")
        assert not _worker_session(worker, "alice")


async def test_a_run_saves_nothing_once_the_role_loses_export(monkeypatch):
    from app.modules.viz import document_service, scheduled_export
    from app.modules.workspaces import service as workspace_module

    documents = SimpleNamespace(permissions=AsyncMock(return_value=["view"]), read=AsyncMock(),
                                workspace_of=AsyncMock())
    files = SimpleNamespace(create_file_bytes=AsyncMock(), create_folder=AsyncMock())
    monkeypatch.setattr(document_service, "native_document_service", documents)
    monkeypatch.setattr(workspace_module, "workspace_service", files)
    with pytest.raises(ExportUnavailable, match="no longer includes Export"):
        await scheduled_export.run(record_for(DASHBOARD, PAGE, "UTC"), {"username": "acct"})
    documents.read.assert_not_awaited()
    files.create_file_bytes.assert_not_awaited()


# ── Table calculations match the editor (calculations.test.ts) ──────────────

def calculated(kind: str, **extra):
    return ChartDefinition.model_validate({
        "chart_id": str(CHART), "title": "Revenue", "dataset_id": str(DATASET),
        "dataset_revision": 1, "visual": "line",
        "encoding": {"kind": "cartesian",
                     "dimensions": [{"field_id": "month", "date_grain": "month"}],
                     "measures": [{"field_id": "revenue", "aggregation": "sum"}],
                     "color": {"field_id": "region"}},
        "table_calculations": [{
            "calculation_id": str(uuid4()), "name": "c", "kind": kind,
            "measure": {"field_id": "revenue", "aggregation": "sum"},
            "addressing": ["month"], "partition_by": ["region"],
            "order": [{"field_id": "month", "direction": "asc"}], **extra}],
    })


def monthly(rows):
    return DatasetQueryResult.model_validate({
        "dataset_id": str(DATASET), "revision": 1, "truncated": False,
        "columns": [
            {"key": "d0", "name": "month", "logical_type": "date", "role": "dimension",
             "field_id": "month", "date_grain": "month"},
            {"key": "d1", "name": "region", "logical_type": "string", "role": "dimension",
             "field_id": "region"},
            {"key": "m0", "name": "revenue", "logical_type": "decimal", "role": "measure",
             "field_id": "revenue", "aggregation": "sum"}],
        "rows": [[{"type": "date", "value": m}, {"type": "string", "value": r},
                  None if v is None else {"type": "decimal", "value": str(v)}] for m, r, v in rows],
    })


SHUFFLED = [("2026-02-01", "Jakarta", 20), ("2026-01-01", "Jakarta", 10),
            ("2026-03-01", "Jakarta", None), ("2026-04-01", "Jakarta", 40),
            ("2026-01-01", "Bandung", 5), ("2026-02-01", "Bandung", 5)]


def by_region(sheet, region):
    rows = [row for row in sheet[2] if row[1] == region]
    rows.sort(key=lambda row: ["Jan", "Feb", "Mar", "Apr"].index(row[0][:3]))
    return [row[2] for row in rows]


@pytest.mark.parametrize("kind, extra, jakarta, bandung", [
    ("running_total", {}, [10, 30, None, 70], [5, 10]),
    ("running_total", {"nulls": "zero"}, [10, 30, 30, 70], [5, 10]),
    ("difference", {}, [None, 10, None, None], [None, 0]),
    ("percent_difference", {}, [None, 100, None, None], [None, 0]),
    ("moving_average", {"window_size": 2}, [10, 15, None, 40], [5, 5]),
    ("percent_of_total", {}, [round(10 / 70 * 100, 9), round(20 / 70 * 100, 9), None,
                              round(40 / 70 * 100, 9)], [50, 50]),
])
def test_scheduled_files_show_the_same_calculations_as_the_screen(kind, extra, jakarta, bandung):
    sheet = result_sheet("Revenue", monthly(SHUFFLED), calculated(kind, **extra))
    assert by_region(sheet, "Jakarta") == jakarta
    assert by_region(sheet, "Bandung") == bandung


def test_ties_share_a_rank_in_files_too():
    tied = monthly([("2026-01-01", "A", 7), ("2026-02-01", "A", 9), ("2026-03-01", "A", 9),
                    ("2026-04-01", "A", 3)])
    assert by_region(result_sheet("R", tied, calculated("rank")), "A") == [3, 1, 1, 4]
    assert by_region(result_sheet("R", tied, calculated("dense_rank")), "A") == [2, 1, 1, 3]
    header = result_sheet("R", tied, calculated("percent_of_total"))[1][2]
    assert header == "Percent of total of revenue (%)"
