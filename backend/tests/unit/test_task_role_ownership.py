from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.task_orchestration import access, router
from app.modules.task_orchestration.credentials import CredentialUnavailable
from app.modules.task_orchestration.dag import GraphState
from app.modules.task_orchestration.ddl import parse_create_task
from app.modules.task_orchestration.execution import DelegateExecutor
from app.modules.task_orchestration.lowering import TaskLoweringError, persist_lowered_task
from app.modules.task_orchestration.worker import GraphRunJob, GraphRunWorker
from tests.unit.test_task_ddl_lowering import FakeLoweringRepository
from tests.unit.test_task_orchestration_api import FakeRepository, make_client
from tests.unit.test_task_orchestration_worker import (
    FakeRepository as WorkerRepository,
)
from tests.unit.test_task_orchestration_worker import (
    RecordingExecutor,
)


def caller(name="bob", role="analyst"):
    return {
        "username": name,
        "roles": [role],
        "assigned_roles": [role],
        "active_role": role,
        "session_id": "caller-session",
    }


@pytest.fixture
def role_api(monkeypatch):
    repo = FakeRepository()
    repo.add_task("root", owner="alice")
    repo.add_task("child", owner="charlie")
    for row in repo.tasks.values():
        row["owner_role"] = "analyst"
    repo.add_edge("db1.default.root", "root", "child")
    repo.add_graph_run("previous", "db1.default.root")
    repo.list_active_graph_runs = AsyncMock(return_value=[])
    repo.create_graph_run = AsyncMock(
        return_value={
            "id": "new",
            "graph_id": "db1.default.root",
            "trigger_type": "manual",
            "state": "pending",
        }
    )
    monkeypatch.setattr(router, "verify_active_role", AsyncMock())
    monkeypatch.setattr(router, "_publish_manual_run", AsyncMock())
    monkeypatch.setattr(router, "write_audit_log", AsyncMock())
    return repo


def test_same_role_different_creators_can_read_sql_history_and_run(role_api):
    client = make_client(role_api, caller())
    for url in [
        "graphs",
        "graphs/db1.default.root",
        "graphs/db1.default.root/runs",
        "graphs/db1.default.root/nodes/id_child/sql",
        "runs/previous",
    ]:
        assert client.get(f"/api/v1/task-orchestration/{url}").status_code == 200
    graph = client.get("/api/v1/task-orchestration/graphs").json()["graphs"][0]
    assert graph["owner_role"] == "analyst" and graph["can_run"]
    assert client.post("/api/v1/task-orchestration/graphs/db1.default.root/runs").status_code == 202
    snapshot = role_api.create_graph_run.call_args.args[0]
    assert (snapshot["execution_user"], snapshot["execution_role"]) == ("bob", "analyst")


@pytest.mark.parametrize("name", ["outsider", "alice"])
def test_wrong_active_role_cannot_read_or_run_even_if_creator(role_api, name):
    user = {**caller(name, "other"), "assigned_roles": ["analyst", "other"]}
    client = make_client(role_api, user)
    assert client.get("/api/v1/task-orchestration/graphs").json()["count"] == 0
    for url in [
        "graphs/db1.default.root",
        "graphs/db1.default.root/nodes/id_child/sql",
        "runs/previous",
    ]:
        assert client.get(f"/api/v1/task-orchestration/{url}").status_code == 404
    assert client.post("/api/v1/task-orchestration/graphs/db1.default.root/runs").status_code == 404
    role_api.create_graph_run.assert_not_awaited()


def test_admin_can_inspect_but_must_activate_owner_role_to_run(role_api):
    client = make_client(role_api, caller("admin", "ACCOUNTADMIN"))
    graph = client.get("/api/v1/task-orchestration/graphs").json()["graphs"][0]
    assert not graph["can_run"]
    assert client.post("/api/v1/task-orchestration/graphs/db1.default.root/runs").status_code == 403


def test_inactive_admin_role_does_not_grant_task_access(role_api):
    user = {**caller("admin", "other"), "roles": ["ACCOUNTADMIN", "other"]}
    assert make_client(role_api, user).get("/api/v1/task-orchestration/graphs").json()["count"] == 0


def test_role_revoked_after_login_blocks_metadata_and_enqueue(role_api, monkeypatch):
    monkeypatch.setattr(
        router, "verify_active_role", AsyncMock(side_effect=HTTPException(403, "revoked"))
    )
    client = make_client(role_api, caller())
    assert client.get("/api/v1/task-orchestration/graphs").status_code == 403
    assert client.post("/api/v1/task-orchestration/graphs/db1.default.root/runs").status_code == 403
    role_api.create_graph_run.assert_not_awaited()


@pytest.mark.parametrize("role", [None, "other"])
def test_mixed_or_unowned_graph_cannot_run(role_api, role):
    role_api.tasks["child"]["owner_role"] = role
    client = make_client(role_api, caller("admin", "ACCOUNTADMIN"))
    assert client.post("/api/v1/task-orchestration/graphs/db1.default.root/runs").status_code == 409


async def test_live_role_check_does_not_trust_cached_assignment(monkeypatch):
    @asynccontextmanager
    async def connection(*args):
        yield object()

    monkeypatch.setattr(access, "decrypt_password", lambda value: "transient")
    monkeypatch.setattr(access.db, "user_conn", connection)
    activate = AsyncMock(side_effect=PermissionError("revoked secret-detail"))
    monkeypatch.setattr(access.role_activation_service, "activate", activate)
    with pytest.raises(HTTPException) as exc:
        await access.verify_active_role({**caller(), "encrypted_password": "encrypted"})
    assert exc.value.status_code == 403
    assert "secret-detail" not in exc.value.detail
    activate.assert_awaited_once()


@pytest.mark.parametrize("change", ["ownership", "mixed", "binding", "missing_snapshot"])
async def test_queued_run_rechecks_ownership_and_service_binding(monkeypatch, change):
    repo = WorkerRepository()
    repo.add_task("A")
    repo.add_task("B")
    repo.add_edge("g", "A", "B")
    repo.add_graph_run("queued", "g")
    if change in {"ownership", "mixed"}:
        repo.tasks["id_A"]["owner_role"] = "new_role"
        if change == "ownership":
            repo.tasks["id_B"]["owner_role"] = "new_role"
    elif change == "binding":
        repo.get_role_execution_user = AsyncMock(return_value="different_service")
    else:
        repo.graph_runs["queued"]["execution_user"] = None
    monkeypatch.setattr("app.modules.task_orchestration.worker.write_audit_log", AsyncMock())
    executor = RecordingExecutor()
    assert (
        await GraphRunWorker(repo, executor).handle(GraphRunJob("queued", "g")) == GraphState.FAILED
    )
    assert executor.submissions == []
    assert any(row["error_message"] for row in repo.task_runs.values())


@pytest.mark.parametrize("parent_role", ["analyst", "other", None])
async def test_dependency_requires_same_role_before_metadata_write(monkeypatch, parent_role):
    repo = FakeLoweringRepository()
    monkeypatch.setattr(
        "app.modules.task_orchestration.lowering.task_orchestration_repository", repo
    )
    parent = parse_create_task(
        "CREATE TASK db1.default.parent AS INSERT INTO t SELECT 1", timezone="UTC"
    )
    await persist_lowered_task(parent, created_by="alice", owner_role=parent_role)
    child = parse_create_task(
        "CREATE TASK db1.default.child AFTER parent AS INSERT INTO t SELECT 2", timezone="UTC"
    )
    if parent_role == "analyst":
        await persist_lowered_task(child, created_by="bob", owner_role="analyst")
        assert len(repo.tasks) == 2
    else:
        with pytest.raises(TaskLoweringError, match="owner role"):
            await persist_lowered_task(child, created_by="bob", owner_role="analyst")
        assert len(repo.tasks) == 1 and repo.edges == []


@pytest.mark.parametrize("session", [None, {"username": "mallory", "encrypted_password": "x"}])
async def test_manual_session_expired_or_wrong_user_never_delegates(monkeypatch, session):
    from app.modules.task_orchestration import execution

    monkeypatch.setattr(execution.session_store, "get", AsyncMock(return_value=session))
    connect = AsyncMock()
    monkeypatch.setattr(execution.db, "user_conn", connect)
    executor = DelegateExecutor(None, impersonation_user="worker", impersonation_password="secret")
    with pytest.raises(CredentialUnavailable, match="session expired"):
        async with executor._owner_conn("bob", "exact-session"):
            pytest.fail("No connection should be opened")
    connect.assert_not_called()


async def test_manual_uses_exact_session_and_authenticated_account(monkeypatch):
    from app.modules.task_orchestration import execution

    lookup = AsyncMock(return_value={"username": "bob", "encrypted_password": "cipher"})
    monkeypatch.setattr(execution.session_store, "get", lookup)
    monkeypatch.setattr(execution, "decrypt_password", lambda value: "ephemeral")
    calls = []

    @asynccontextmanager
    async def connect(username, password):
        calls.append((username, password))
        yield "user-connection"

    monkeypatch.setattr(execution.db, "user_conn", connect)
    executor = DelegateExecutor(
        None, impersonation_user="worker", impersonation_password="worker-secret"
    )
    async with executor._owner_conn("bob", "exact-session") as conn:
        assert conn == "user-connection"
    lookup.assert_awaited_once_with("exact-session")
    assert calls == [("bob", "ephemeral")]
