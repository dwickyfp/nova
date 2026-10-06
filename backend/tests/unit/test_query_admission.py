"""``QUERY_MAX_CONCURRENCY`` refuses statements over the per-process cap."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.modules.query import router as query_router
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


@pytest.fixture
def execute(monkeypatch):
    admission = QueryAdmission()
    statements = AsyncMock(return_value=[])
    audit = AsyncMock()
    monkeypatch.setattr(query_router, "query_admission", admission)
    monkeypatch.setattr(query_router.query_service, "execute_statements", statements)
    monkeypatch.setattr(query_router, "write_audit_log", audit)
    monkeypatch.setattr(query_router, "_resolve_active_role", lambda user: user["active_role"])
    return admission, statements, audit


async def test_execute_refuses_with_429_and_audits_the_refusal(monkeypatch, execute):
    admission, statements, audit = execute
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 1)
    request = query_router.QueryRequest(sql="SELECT 1", database="analytics")

    async with admission.slot():
        with pytest.raises(HTTPException) as refused:
            await query_router.execute_query(request, USER)

    assert refused.value.status_code == 429
    assert refused.value.headers == {"Retry-After": "1"}
    statements.assert_not_awaited()
    audit.assert_awaited_once()
    entry = audit.await_args.kwargs
    assert entry["status"] == "REFUSED" and entry["user_name"] == "alice"
    assert entry["sql_text"] == "SELECT 1" and entry["database_name"] == "analytics"


async def test_execute_runs_and_frees_its_slot_under_the_cap(monkeypatch, execute):
    admission, statements, audit = execute
    monkeypatch.setattr(settings, "QUERY_MAX_CONCURRENCY", 1)
    request = query_router.QueryRequest(sql="SELECT 1")

    assert await query_router.execute_query(request, USER) == []
    assert await query_router.execute_query(request, USER) == []

    assert statements.await_count == 2 and admission.active == 0
    audit.assert_not_awaited()
