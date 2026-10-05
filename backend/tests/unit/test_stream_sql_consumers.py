from unittest.mock import AsyncMock

import pytest

from app.modules.query.service import QueryService, query_service
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.session import SessionState
from app.sql_frontend.capabilities.starrocks import EngineCapabilities


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize(
    "sql",
    [
        "CREATE STREAM analytics.default.s ON TABLE analytics.default.t APPEND_ONLY=TRUE",
        "SHOW STREAMS",
        "DESCRIBE STREAM analytics.default.s",
        "DESC STREAM analytics.s",
        "SHOW STREAM BACKLOG analytics.s",
        "CREATE TASK t WHEN NOVA_STREAM_HAS_DATA('analytics.default.s') "
        "AS INSERT INTO sink SELECT 1",
        "SHOW STREAM STATUS analytics.default.s",
        "SELECT NOVA_STREAM_HAS_DATA('analytics.default.s')",
    ],
)
async def test_disabled_stream_sql_keeps_public_source(monkeypatch, proxy, sql, enabled):
    monkeypatch.setattr("app.modules.streams.runtime.settings.STREAMS_ENABLED", enabled)
    metadata = AsyncMock(side_effect=AssertionError("Metadata mutation or read"))
    monkeypatch.setattr("app.core.database.db.execute_system", metadata)
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    repository = AsyncMock()
    if proxy:
        monkeypatch.setattr(query_service, "_repo", repository)
        monkeypatch.setattr(
            query_service, "_capability_resolver", AsyncMock(return_value=EngineCapabilities())
        )
        result = await ProxyQueryExecutor(
            SessionState(database="analytics", active_role="reader"),
        ).execute(sql, username="alice", connection=object())
    else:
        service = QueryService(capability_resolver=AsyncMock(return_value=EngineCapabilities()))
        service._repo = repository
        result = await service.execute(sql, "alice", "enc", database="analytics", role="reader")
    assert result.error and ("unavailable" if enabled else "disabled") in result.error
    if not proxy:
        assert result.original_sql == sql
    assert any(call.kwargs.get("sql_text") == sql for call in audit.await_args_list)
    repository.execute_as_user.assert_not_awaited()
    metadata.assert_not_awaited()


@pytest.mark.parametrize("conditional", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_drop_confirmation_precedes_admission(monkeypatch, conditional, confirmed, enabled):
    from app.sql_frontend.errors import ConfirmationRequiredError

    monkeypatch.setattr("app.modules.streams.runtime.settings.STREAMS_ENABLED", enabled)
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    metadata = AsyncMock(side_effect=AssertionError("Metadata touched"))
    monkeypatch.setattr("app.core.database.db.execute_system", metadata)
    service = QueryService(capability_resolver=AsyncMock(return_value=EngineCapabilities()))
    service._repo = AsyncMock()
    sql = "DROP STREAM " + ("IF EXISTS " if conditional else "") + "analytics.s"
    if not confirmed:
        with pytest.raises(ConfirmationRequiredError):
            await service.execute(sql, "alice", "enc", database="analytics", role="reader")
    else:
        result = await service.execute(
            sql,
            "alice",
            "enc",
            database="analytics",
            role="reader",
            confirm_destructive=True,
        )
        assert ("unavailable" if enabled else "disabled") in result.error
        assert result.original_sql == sql
    service._repo.execute_as_user.assert_not_awaited()
    metadata.assert_not_awaited()


@pytest.mark.parametrize("sql", ["DROP STREAM analytics.s", "DROP STREAM IF EXISTS analytics.s"])
async def test_proxy_drop_is_refused_without_engine_submission(monkeypatch, sql):
    monkeypatch.setattr("app.modules.query.service.write_audit_log", AsyncMock())
    repository = AsyncMock()
    monkeypatch.setattr(query_service, "_repo", repository)
    result = await ProxyQueryExecutor(
        SessionState(database="analytics", active_role="reader"),
    ).execute(sql, username="alice", connection=object())
    assert result.error and "confirm" in result.error.lower()
    repository.execute_as_user.assert_not_awaited()
