"""Release QA: a scheduled export never reports success when its file was not saved.

NDV-STORE-010: the workspace store is unavailable during a scheduled run. The
run fails, its failure reaches the task (no "saved" result) and no file is
recorded. Native Dataviz has no object store or signed URLs; the scheduled
workbook is saved as one workspace file.

NDV-STORE-003: one scheduled occurrence saves exactly one file however often
it is retried. The retries go through the task worker's handler runner, as in
production: a crash before the save, a crash after the save before the run is
marked done, an unknown save outcome, a retry in a later minute or the same
one, and a concurrent stale attempt. A retry verifies the file it finds; a
partial or changed leftover is never reported as done.
"""

import asyncio
import io
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import StorageError
from app.core.metadata_db import MetadataOutcomeUnknown
from app.modules.viz import scheduled_export
from app.modules.viz.contracts.queries import DatasetQueryResult
from app.modules.viz.scheduled_export import FOLDER, record_for
from tests.unit.test_native_viz_scheduled_export import (
    CHART,
    DASHBOARD,
    DATASET,
    PAGE,
    board,
    chart,
)

ACCOUNT = {"username": "nova_task_service_mkt", "active_role": "marketing",
           "session_id": "task-run", "security_context_version": 1,
           "assigned_roles": ["marketing"]}


def _result() -> DatasetQueryResult:
    return DatasetQueryResult.model_validate({
        "dataset_id": str(DATASET), "revision": 1, "truncated": False,
        "columns": [
            {"key": "d0", "name": "region", "logical_type": "string", "role": "dimension",
             "field_id": "region"},
            {"key": "m0", "name": "revenue", "logical_type": "decimal", "role": "measure",
             "field_id": "revenue", "aggregation": "sum"}],
        "rows": [[{"type": "string", "value": "West"}, {"type": "decimal", "value": "12.5"}]],
    })


@pytest.fixture
def wired(monkeypatch):
    from app.modules.viz import dataset_service, document_service
    from app.modules.workspaces import service as workspace_module
    from app.modules.workspaces import spaces

    bar = chart({"kind": "cartesian", "dimensions": [{"field_id": "region"}],
                 "measures": [{"field_id": "revenue", "aggregation": "sum"}]})

    async def read(user, identity, *, draft=False, revision=None):
        return {"definition": board() if identity == DASHBOARD else bar}

    documents = SimpleNamespace(
        permissions=AsyncMock(return_value=["view", "export"]),
        read=AsyncMock(side_effect=read),
        workspace_of=AsyncMock(return_value="0123456789abcdef0123456789abcdef"),
    )
    datasets = SimpleNamespace(query=AsyncMock(return_value=_result()))
    files = SimpleNamespace(create_folder=AsyncMock(return_value={"id": "folder"}),
                            create_file_bytes=AsyncMock(return_value={"id": "entry-1"}),
                            list_entries=AsyncMock(return_value=[]),
                            read_entry_bytes=AsyncMock())
    space = SimpleNamespace(namespace="space:sales")
    monkeypatch.setattr(document_service, "native_document_service", documents)
    monkeypatch.setattr(dataset_service, "native_dataset_service", datasets)
    monkeypatch.setattr(workspace_module, "workspace_service", files)
    monkeypatch.setattr(spaces.space_service, "resolve", AsyncMock(return_value=space))
    return SimpleNamespace(documents=documents, datasets=datasets, files=files)


async def _run():
    return await scheduled_export.run(record_for(DASHBOARD, PAGE, "UTC"), ACCOUNT)


async def test_a_healthy_run_reports_the_entry_it_saved(wired):
    outcome = await _run()
    assert outcome["status"] == "saved" and outcome["entry_id"] == "entry-1"
    wired.files.create_file_bytes.assert_awaited_once()
    namespace, folder, name, data = wired.files.create_file_bytes.await_args.args
    assert (namespace, folder) == ("space:sales", FOLDER) and name.endswith(".xlsx")
    assert outcome["bytes"] == len(data) > 0
    assert wired.documents.read.await_args_list[1].args[1] == CHART


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_store_failure_while_saving_fails_the_run(wired, status):
    wired.files.create_file_bytes.side_effect = StorageError("store unreachable", status)
    with pytest.raises(StorageError) as error:
        await _run()
    assert error.value.status_code == status
    # One attempt, no silent retry, no "saved" result for the task to record.
    wired.files.create_file_bytes.assert_awaited_once()


@pytest.mark.parametrize("status", [500, 503])
async def test_a_store_failure_creating_the_folder_saves_nothing(wired, status):
    wired.files.create_folder.side_effect = StorageError("store unreachable", status)
    with pytest.raises(StorageError):
        await _run()
    wired.files.create_file_bytes.assert_not_awaited()


async def test_an_existing_folder_is_not_a_failure(wired):
    wired.files.create_folder.side_effect = StorageError("exists", 409)
    outcome = await _run()
    assert outcome["status"] == "saved"
    wired.files.create_file_bytes.assert_awaited_once()


async def test_a_query_failure_never_reaches_the_store(wired):
    wired.datasets.query.side_effect = RuntimeError("engine unavailable")
    with pytest.raises(RuntimeError):
        await _run()
    wired.files.create_folder.assert_not_awaited()
    wired.files.create_file_bytes.assert_not_awaited()


async def test_the_task_handler_propagates_the_store_failure(wired):
    from app.modules.task_orchestration.internal_handlers import (
        InternalTaskConfiguration,
        Scope,
        run_once,
    )

    wired.files.create_file_bytes.side_effect = StorageError("store unreachable", 503)
    config = InternalTaskConfiguration(scope=Scope.from_user(ACCOUNT),
                                       record_id=record_for(DASHBOARD, PAGE, "UTC"))
    with pytest.raises(StorageError):
        await run_once("viz.dashboard_export", config, ACCOUNT)
    wired.files.create_file_bytes.assert_awaited_once()


# ── NDV-STORE-003: one occurrence, one file ────────────────────────────────

DUE = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
OCCURRENCE = "3f2a9c1b-0000-5000-8000-000000000003"


class Store:
    """The workspace file store as the run sees it: entries plus their bytes."""

    def __init__(self):
        self.entries: list[dict] = []
        self.data: dict[str, bytes] = {}
        self.writes = 0
        #: Fails the next save: "before" stores nothing, "unknown" stores then fails.
        self.crash: str | None = None

    def add(self, name: str, data: bytes, *, size: int | None = None) -> dict:
        entry = {"id": f"entry-{len(self.entries) + 1}", "name": name,
                 "parent_path": FOLDER, "path": f"{FOLDER}/{name}", "entry_type": "file",
                 "size_bytes": len(data) if size is None else size}
        self.entries.append(entry)
        self.data[entry["id"]] = data
        return entry

    async def create_folder(self, namespace, parent, name):
        raise StorageError("exists", 409)

    async def create_file_bytes(self, namespace, parent, name, data):
        self.writes += 1
        crash, self.crash = self.crash, None
        if crash == "before":
            raise ConnectionError("worker lost before the save")
        await asyncio.sleep(0)  # Let a concurrent attempt interleave.
        if any(entry["name"] == name for entry in self.entries):
            raise StorageError("A file or folder with this name already exists here", 409)
        entry = self.add(name, data)
        if crash == "unknown":
            raise MetadataOutcomeUnknown()
        return entry

    async def list_entries(self, namespace):
        return [dict(entry) for entry in self.entries]

    async def read_entry_bytes(self, namespace, entry_id):
        return next(e for e in self.entries if e["id"] == entry_id), self.data[entry_id]


@pytest.fixture
def scheduled(wired, monkeypatch):
    """The task worker's runner for one scheduled occurrence, with a settable clock."""
    from app.modules.task_orchestration import internal_handlers
    from app.modules.workspaces import service as workspace_module

    store = Store()
    monkeypatch.setattr(workspace_module, "workspace_service", store)
    clock = SimpleNamespace(now=DUE + timedelta(seconds=4))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.now if tz is None else clock.now.astimezone(tz)

    monkeypatch.setattr(scheduled_export, "datetime", Clock)
    record = record_for(DASHBOARD, PAGE, "UTC")
    task = {"id": "export-task", "name": "export", "version": 1, "schedule_kind": "cron",
            "handler": "viz.dashboard_export",
            "handler_config": {"version": 1, "record_id": record, "scope": {
                "principal": ACCOUNT["username"], "active_role": ACCOUNT["active_role"],
                "security_context_version": 1}}}
    job = SimpleNamespace(graph_run_id=OCCURRENCE, trigger_type="schedule",
                          execution_user=ACCOUNT["username"],
                          execution_role=ACCOUNT["active_role"], execution_session_id=None)
    repository = SimpleNamespace(
        get_task=AsyncMock(return_value=task),
        get_task_run=AsyncMock(return_value={"state": "running"}),
        get_graph_run=AsyncMock(return_value={"state": "running",
                                              "started_at": DUE + timedelta(seconds=4)}),
        get_role_execution_user=AsyncMock(return_value=ACCOUNT["username"]),
        scheduled_occurrence_time=AsyncMock(return_value=DUE),
    )

    class Connection:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_):
            return False

    executor = SimpleNamespace(owner_connection=lambda _bound: Connection(),
                               _prepare_task_session=AsyncMock())
    monkeypatch.setattr(internal_handlers, "_dict_cursor", lambda _connection: Connection())

    async def attempt(at: datetime | None = None):
        if at is not None:
            clock.now = at
        await internal_handlers.run_internal_task(
            task, job, "node-run", executor, repository, renew=AsyncMock())

    return SimpleNamespace(store=store, attempt=attempt, wired=wired, repository=repository)


def _names(store: Store) -> list[str]:
    return [entry["name"] for entry in store.entries]


async def test_a_retry_in_a_later_minute_after_the_save_reports_the_saved_file(scheduled):
    await scheduled.attempt()
    # The worker died before marking the run done; the node is taken again later.
    await scheduled.attempt(DUE + timedelta(minutes=7))
    assert _names(scheduled.store) == ["Sales Overview 2026-11-16 0800.xlsx"]
    assert scheduled.store.writes == 1
    # The retry verified the saved file instead of querying and saving again.
    assert scheduled.wired.datasets.query.await_count == 1


async def test_a_retry_in_the_same_minute_after_the_save_does_not_clash(scheduled):
    await scheduled.attempt()
    await scheduled.attempt(DUE + timedelta(seconds=40))
    assert _names(scheduled.store) == ["Sales Overview 2026-11-16 0800.xlsx"]
    assert scheduled.store.writes == 1


async def test_a_crash_before_the_save_saves_once_named_by_the_due_time(scheduled):
    scheduled.store.crash = "before"
    with pytest.raises(ConnectionError):
        await scheduled.attempt()
    assert scheduled.store.entries == []
    await scheduled.attempt(DUE + timedelta(minutes=12))
    await scheduled.attempt(DUE + timedelta(minutes=13))
    assert _names(scheduled.store) == ["Sales Overview 2026-11-16 0800.xlsx"]


async def test_an_unknown_save_outcome_is_verified_not_written_again(scheduled):
    scheduled.store.crash = "unknown"
    with pytest.raises(MetadataOutcomeUnknown):
        await scheduled.attempt()
    await scheduled.attempt(DUE + timedelta(minutes=3))
    assert _names(scheduled.store) == ["Sales Overview 2026-11-16 0800.xlsx"]
    assert scheduled.store.writes == 1


async def test_a_concurrent_stale_attempt_reports_the_same_single_file(scheduled):
    await asyncio.gather(scheduled.attempt(), scheduled.attempt())
    assert _names(scheduled.store) == ["Sales Overview 2026-11-16 0800.xlsx"]


async def test_the_saved_file_carries_its_run_and_a_matching_digest(scheduled):
    await scheduled.attempt()
    entry = scheduled.store.entries[0]
    data = scheduled.store.data[entry["id"]]
    occurrence = scheduled_export.Occurrence(OCCURRENCE, DUE)
    assert scheduled_export.check_saved(entry, data, occurrence) == "saved"
    assert scheduled_export.check_saved(
        entry, data, scheduled_export.Occurrence(str(uuid4()), DUE)) == "other"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        receipt = archive.read("docProps/custom.xml").decode()
    assert OCCURRENCE in receipt


def _changed(data: bytes) -> bytes:
    """The same workbook with one sheet altered and the receipt kept."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for name in source.namelist():
            body = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                body = body.replace(b"West", b"East")
            target.writestr(name, body)
    return out.getvalue()


@pytest.mark.parametrize("leftover", ["truncated", "size", "changed"])
async def test_a_partial_or_changed_leftover_is_never_reported_as_done(scheduled, leftover):
    store = scheduled.store
    await scheduled.attempt()
    entry = store.entries[0]
    good = store.data[entry["id"]]
    if leftover == "truncated":
        store.data[entry["id"]] = good[: len(good) // 2]
        entry["size_bytes"] = len(good) // 2
    elif leftover == "size":
        entry["size_bytes"] = len(good) + 1
    else:
        store.data[entry["id"]] = _changed(good)
        entry["size_bytes"] = len(store.data[entry["id"]])
    with pytest.raises(scheduled_export.ExportUnavailable, match="not marked done"):
        await scheduled.attempt(DUE + timedelta(minutes=9))
    # Nothing new was written over or beside the leftover.
    assert store.writes == 1 and len(store.entries) == 1


async def test_another_runs_file_with_the_same_name_is_not_taken_as_this_runs(scheduled):
    """Two roles can schedule the same page at the same time into one workspace."""
    store = scheduled.store
    other = scheduled_export.workbook([("Revenue", ["Region"], [["North"]])], run=str(uuid4()))
    store.add("Sales Overview 2026-11-16 0800.xlsx", other)
    await scheduled.attempt()
    await scheduled.attempt(DUE + timedelta(minutes=5))
    assert _names(store) == ["Sales Overview 2026-11-16 0800.xlsx",
                             "Sales Overview 2026-11-16 0800 3f2a9c1b.xlsx"]
    assert store.data["entry-1"] == other


async def test_a_run_without_a_scheduled_time_is_named_by_its_recorded_start(scheduled):
    scheduled.repository.scheduled_occurrence_time.return_value = None
    scheduled.repository.get_graph_run.return_value = {
        "state": "running", "started_at": datetime(2026, 11, 16, 9, 30, 2, tzinfo=UTC)}
    await scheduled.attempt(datetime(2026, 11, 16, 9, 30, 5, tzinfo=UTC))
    await scheduled.attempt(datetime(2026, 11, 16, 9, 45, tzinfo=UTC))
    assert _names(scheduled.store) == ["Sales Overview 2026-11-16 0930.xlsx"]
