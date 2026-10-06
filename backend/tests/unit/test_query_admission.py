"""``QUERY_MAX_CONCURRENCY`` refuses statements over the per-process cap."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.modules.query import admission
from app.modules.query.admission import QueryAdmission, QueryCapacityError

USER = {
    "username": "alice",
    "encrypted_password": "enc",
    "session_id": "session-1",
    "active_role": "analyst",
}


async def test_no_cap_admits_every_statement(monkeypatch):
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 0)
    admission = QueryAdmission()

    async with admission.slot(), admission.slot(), admission.slot():
        assert admission.active == 3
    assert admission.active == 0


async def test_statement_over_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 2)
    admission = QueryAdmission()

    async with admission.slot(), admission.slot():
        with pytest.raises(QueryCapacityError):
            async with admission.slot():
                pytest.fail("a third statement was admitted")
        # The refusal must not consume or free a slot it never held.
        assert admission.active == 2


@pytest.mark.parametrize("outcome", ["success", "error", "cancelled"])
async def test_slot_is_released_however_the_statement_ends(monkeypatch, outcome):
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 1)
    admission = QueryAdmission()

    async def statement():
        async with admission.slot():
            if outcome == "error":
                raise RuntimeError("engine failure")
            if outcome == "cancelled":
                await asyncio.Event().wait()

    task = asyncio.create_task(statement())
    await asyncio.sleep(0)
    if outcome == "cancelled":
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert admission.active == 0
    async with admission.slot():
        assert admission.active == 1


def _request(path: str = "/api/v1/query/execute") -> SimpleNamespace:
    return SimpleNamespace(url=SimpleNamespace(path=path))


@pytest.fixture
def gate(monkeypatch):
    fresh = QueryAdmission()
    audit = AsyncMock()
    monkeypatch.setattr(admission, "query_admission", fresh)
    monkeypatch.setattr(admission, "write_audit_log", audit)
    return fresh, audit


async def test_route_dependency_refuses_with_429_and_audits_the_refusal(monkeypatch, gate):
    fresh, audit = gate
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 1)

    async with fresh.slot():
        with pytest.raises(HTTPException) as refused:
            await anext(admission.admitted(_request(), USER))

    assert refused.value.status_code == 429
    assert refused.value.headers == {"Retry-After": "1"}
    entry = audit.await_args.kwargs
    assert entry["status"] == "REFUSED" and entry["user_name"] == "alice"
    assert entry["object_name"] == "/api/v1/query/execute" and entry["session_id"] == "session-1"
    # The refusal neither took a slot nor gave one back.
    assert fresh.active == 0


@pytest.mark.parametrize("failure", [None, RuntimeError("engine failure")])
async def test_route_dependency_holds_its_slot_for_the_request(monkeypatch, gate, failure):
    fresh, audit = gate
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 1)
    dependency = admission.admitted(_request(), USER)

    await anext(dependency)
    assert fresh.active == 1
    if failure is None:
        with pytest.raises(StopAsyncIteration):
            await anext(dependency)
    else:
        # FastAPI throws the handler's error back into the dependency.
        with pytest.raises(RuntimeError):
            await dependency.athrow(failure)

    assert fresh.active == 0
    audit.assert_not_awaited()


def _gated_routes(router) -> set[str]:
    return {
        route.path
        for route in router.routes
        if any(dependency.call is admission.admitted for dependency in route.dependant.dependencies)
    }


def test_every_execution_route_is_behind_the_cap():
    from app.modules.ml_engine.router import router as ml_router
    from app.modules.query.router import router as query_router

    assert _gated_routes(query_router) == {"/execute", "/explain"}
    assert _gated_routes(ml_router) == {
        "/train",
        "/predict/batch",
        "/forecast",
        "/forecast/version",
        "/predict/materialize",
        "/execute",
        "/runs/{run_id}/promote",
    }


def test_the_cap_is_on_by_default():
    from app.core.config import Settings

    assert Settings.model_fields["QUERY_MAX_CONCURRENCY"].default == 64


async def test_refusals_and_running_statements_are_counted(monkeypatch):
    from app.observability.metrics import QUERY_ADMISSION_ACTIVE, QUERY_ADMISSION_REFUSED

    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 1)
    fresh = QueryAdmission()
    refused = QUERY_ADMISSION_REFUSED.labels(source="mysql_proxy")
    before = refused._value.get()

    async with fresh.slot("mysql_proxy"):
        assert QUERY_ADMISSION_ACTIVE._value.get() == 1
        with pytest.raises(QueryCapacityError):
            async with fresh.slot("mysql_proxy"):
                pytest.fail("admitted over the cap")

    assert refused._value.get() == before + 1
    assert QUERY_ADMISSION_ACTIVE._value.get() == 0


class _Service:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def preflight_script(self, statements, **kwargs):
        return None

    async def execute_statements(self, **kwargs):
        from app.modules.query.repository import QueryResult

        self.statements.append(kwargs["sql"])
        return [QueryResult(columns=["1"], rows=[[1]], row_count=1)]


@pytest.fixture
def proxy(monkeypatch):
    from app.proxy import executor
    from app.proxy.session import SessionState

    fresh = QueryAdmission()
    service = _Service()
    audit = AsyncMock()
    monkeypatch.setattr(executor, "query_admission", fresh)
    monkeypatch.setattr(executor, "query_service", service)
    monkeypatch.setattr(admission, "write_audit_log", audit)
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 1)
    session = SessionState()
    return executor.ProxyQueryExecutor(session), session, fresh, service, audit


async def test_proxy_refuses_a_statement_over_the_cap_and_keeps_the_session(proxy):
    executor, _, fresh, service, audit = proxy

    async with fresh.slot():
        refused = await executor.execute("SELECT 1", username="alice", connection=object())
    accepted = await executor.execute("SELECT 1", username="alice", connection=object())

    assert refused.error and "maximum number of queries" in refused.error
    assert accepted.error is None and service.statements == ["SELECT 1"]
    audit.assert_awaited_once()
    entry = audit.await_args.kwargs
    assert entry["status"] == "REFUSED" and entry["object_type"] == "mysql_proxy"
    assert fresh.active == 0


async def test_proxy_never_refuses_inside_an_open_transaction(proxy):
    executor, session, fresh, service, audit = proxy
    session.in_transaction = True

    async with fresh.slot():
        result = await executor.execute(
            "INSERT INTO t VALUES (1)", username="alice", connection=object()
        )

    assert result.error is None and service.statements == ["INSERT INTO t VALUES (1)"]
    audit.assert_not_awaited()
