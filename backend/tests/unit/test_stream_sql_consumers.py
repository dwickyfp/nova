from unittest.mock import AsyncMock

import pytest

from app.modules.query.service import QueryService, query_service
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.session import SessionState


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("sql", [
    "CREATE STREAM analytics.default.s ON TABLE analytics.default.t APPEND_ONLY=TRUE",
    "SHOW STREAMS",
    "CREATE TASK t WHEN NOVA_STREAM_HAS_DATA('analytics.default.s') AS INSERT INTO sink SELECT 1",
    "SHOW STREAM STATUS analytics.default.s",
    "SELECT NOVA_STREAM_HAS_DATA('analytics.default.s')",
])
async def test_disabled_stream_sql_keeps_public_source(monkeypatch, proxy, sql):
    monkeypatch.setattr("app.modules.streams.runtime.settings.STREAMS_ENABLED", False)
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    repository = AsyncMock()
    if proxy:
        monkeypatch.setattr(query_service, "_repo", repository)
        result = await ProxyQueryExecutor(
            SessionState(database="analytics", active_role="reader"),
        ).execute(sql, username="alice", connection=object())
    else:
        service = QueryService()
        service._repo = repository
        result = await service.execute(sql, "alice", "enc", database="analytics", role="reader")
    assert result.error and "disabled" in result.error
    if not proxy:
        assert result.original_sql == sql
    assert any(call.kwargs.get("sql_text") == sql for call in audit.await_args_list)
    repository.execute_as_user.assert_not_awaited()
