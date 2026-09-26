from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import deps
from app.core.exceptions import register_exception_handlers
from app.modules.monitoring import ai_usage
from app.modules.monitoring.ai_usage import AIUsageService, _event, sql_actions
from app.modules.monitoring.router import router

NOW = datetime(2026, 9, 25, 12, tzinfo=UTC)


@pytest.mark.parametrize(("sql", "expected"), [
    ("SELECT AI_COMPLETE('hi'), AI_SENTIMENT(txt) FROM t", ["AI_COMPLETE", "AI_SENTIMENT"]),
    ("SELECT 'AI_COMPLETE(secret)', 1 /* AI_SENTIMENT(x) */", []),
    ("SELECT AI_COMPLETE('a'), AI_COMPLETE('b')", ["AI_COMPLETE"]),
    ("SELECT `AI_COMPLETE`('hi')", ["AI_COMPLETE"]),
    ("UPDATE t SET label = AI_CLASSIFY(txt, 'a,b')", ["AI_CLASSIFY"]),
    ("DELETE FROM t WHERE AI_FILTER(txt, 'obsolete') = 'true'", ["AI_FILTER"]),
    ("EXPLAIN SELECT AI_COMPLETE('hi')", []),
    ("CREATE FUNCTION AI_COMPLETE(x STRING) RETURNS STRING AS x", []),
    ("CREATE TABLE t AS SELECT AI_COMPLETE('hi') AS answer", ["AI_COMPLETE"]),
    ("SELECT 'x\\\' AI_COMPLETE(password)'", []),
])
def test_detect_only_executable_functions(sql, expected):
    assert sql_actions(sql) == expected


def event(name="one", at="2026-09-25T10:00:00", **kwargs):
    values = dict(user="alice", source="assistant", action="Nova Studio / Assistant",
                  model="model-a", prompt=100, completion=20, total=120, status="recorded")
    return _event(name, at, **{**values, **kwargs})


@pytest.fixture
def service(monkeypatch):
    service = AIUsageService()
    for method in ("_messages", "_runs", "_functions"):
        monkeypatch.setattr(service, method, AsyncMock(return_value=([], False)))
    return service


async def test_summary_missing_usage_comparison_and_zero_days(service):
    service._messages.side_effect = [
        ([event(), event("two", model="model-b", prompt=50, completion=10, total=60)], False),
        ([event("prior", at="2026-09-18T10:00:00", total=90)], False),
    ]
    service._functions.side_effect = [
        ([event("query", source="functions", action="AI_COMPLETE", model=None,
                prompt=None, completion=None, total=None, status="error")], False),
        ([], False),
    ]
    data = await service.dashboard(now=NOW)
    assert data["summary"]["total_tokens"] == 180
    assert data["summary"]["activities"] == 3
    assert data["summary"]["coverage_percent"] == 66.7
    assert data["summary"]["avg_tokens"] == 90
    assert data["summary"]["failed_activities"] == 1
    assert data["token_change_percent"] == 100
    assert len(data["daily"]) == 7
    assert data["daily"][0]["total_tokens"] == 0
    assert data["models"][0]["name"] == "model-a"
    assert data["activities"][-1]["total_tokens"] is None or any(
        row["total_tokens"] is None for row in data["activities"]
    )
    begin, end = service._messages.call_args_list[1].args
    assert begin == datetime(2026, 9, 12, tzinfo=UTC)
    assert end == datetime(2026, 9, 18, 12, tzinfo=UTC)


async def test_filters_apply_to_every_aggregate_and_pagination(service):
    service._messages.return_value = ([event(), event("two", user="bob")], False)
    data = await service.dashboard(now=NOW, user_name="bob", offset=1, limit=1)
    assert data["summary"]["activities"] == 1
    assert data["users"][0]["name"] == "bob"
    assert data["total"] == 1
    assert data["activities"] == []
    assert data["filters"]["users"] == ["alice", "bob"]


async def test_partial_data_never_has_a_comparison(service):
    service._messages.return_value = ([event()], True)
    service._runs.side_effect = RuntimeError("sensitive database error")
    data = await service.dashboard(now=NOW)
    assert data["partial"]
    assert data["previous_summary"] is None
    assert data["token_change_percent"] is None
    assert "sensitive" not in str(data)


async def test_all_failed_is_unavailable_not_zero(service):
    for method in (service._messages, service._runs, service._functions):
        method.side_effect = RuntimeError("offline")
    with pytest.raises(ai_usage.AIUsageUnavailable):
        await service.dashboard(now=NOW)


async def test_smart_counts_are_not_assumed_measured_zero(monkeypatch):
    execute = AsyncMock(return_value={"rows": [
        ("root", NOW, "alice", 0, "completed", 100, 25),
        ("child", NOW, "alice", 1, "failed", 0, 0),
    ]})
    monkeypatch.setattr(ai_usage.db, "execute_system", execute)
    events, _ = await AIUsageService()._runs(NOW, NOW)
    assert "OR agent_id IN ('__smart__', '__auto__')" in execute.call_args.args[0]
    assert events[0]["total_tokens"] == 125
    assert events[0]["model"] is None
    assert events[1]["total_tokens"] is None


async def test_message_query_excludes_smart_summary_and_projects_no_content(monkeypatch):
    execute = AsyncMock(return_value={"rows": []})
    monkeypatch.setattr(ai_usage.db, "execute_system", execute)
    await AIUsageService()._messages(NOW, NOW)
    sql, params = execute.call_args.args
    assert "NOT IN ('__smart__', '__auto__')" in sql
    assert "content" not in sql
    assert "steps" not in sql
    assert params[-1] == ai_usage.SOURCE_LIMIT + 1


def test_null_and_zero_are_distinct():
    assert event(prompt=0, completion=0, total=None)["total_tokens"] == 0
    assert event(prompt=5, completion=None, total=None)["total_tokens"] is None
    assert event(prompt=-1, completion=True, total=None)["total_tokens"] is None


async def test_sql_history_normalizes_database_timezone_and_never_returns_sql(monkeypatch):
    execute = AsyncMock(return_value={"rows": [
        ("q1", NOW.replace(tzinfo=None), "alice",
         "SELECT AI_COMPLETE('private prompt')", "SUCCESS", 40),
        ("q2", NOW.replace(tzinfo=None), "alice", "SELECT 'AI_COMPLETE(secret)'", "SUCCESS", 10),
    ]})
    monkeypatch.setattr(ai_usage.db, "execute_system", execute)
    monkeypatch.setattr(ai_usage, "configured_timezone", lambda: "Asia/Jakarta")
    events, limited = await AIUsageService()._functions(NOW, NOW)
    sql, params = execute.call_args.args
    assert "CONVERT_TZ(event_time, %s, '+00:00')" in sql
    assert "event_time >= CONVERT_TZ(%s, '+00:00', %s)" in sql
    assert params[0] == params[2] == params[4] == "Asia/Jakarta"
    assert len(events) == 1
    assert events[0]["at"] == NOW.isoformat()
    assert events[0]["total_tokens"] is None
    assert "private prompt" not in str(events)
    assert "sql_text" not in events[0]
    assert not limited


@pytest.mark.parametrize(("role", "status"), [("analyst", 403), ("ACCOUNTADMIN", 200)])
def test_api_role_boundary(monkeypatch, role, status):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[deps.get_current_user] = lambda: {
        "username": "test", "roles": [role], "active_role": role,
    }
    dashboard = AsyncMock(return_value={"summary": {}})
    monkeypatch.setattr(ai_usage.ai_usage_service, "dashboard", dashboard)
    with TestClient(app) as client:
        response = client.get("/ai/usage")
    assert response.status_code == status
    assert dashboard.call_count == int(status == 200)


def test_api_bounds_and_no_exception_details(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[deps.get_current_user] = lambda: {
        "username": "test", "roles": ["ACCOUNTADMIN"], "active_role": "ACCOUNTADMIN",
    }
    monkeypatch.setattr(ai_usage.ai_usage_service, "dashboard", AsyncMock(
        side_effect=ai_usage.AIUsageUnavailable("secret")
    ))
    with TestClient(app) as client:
        assert client.get("/ai/usage?days=31").status_code == 422
        assert client.get("/ai/usage?source=invalid").status_code == 422
        assert client.get("/ai/usage?limit=1000").status_code == 422
        response = client.get("/ai/usage")
        assert response.status_code == 503
        assert "secret" not in response.text
