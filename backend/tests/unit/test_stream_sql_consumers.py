"""Stream SQL through both consumers (API and MySQL proxy).

Stream statements are Nova's: they reach the Streams service, never the engine
as written, and the audit records the caller's statement. A stream function
reaches the engine only as the TRUE/FALSE it evaluated to.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.query.service import QueryService, query_service
from app.modules.streams.namespace import StreamName
from app.modules.streams.schemas import StreamError
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.session import SessionState
from app.sql_frontend.capabilities.starrocks import EngineCapabilities


@pytest.fixture
def streams(monkeypatch):
    from app.modules.streams import service as service_module
    from app.modules.streams import sql as sql_module
    from app.modules.streams.sources import source_registry

    fake = SimpleNamespace(
        create=AsyncMock(side_effect=StreamError(
            "STREAM_CHANGE_TRACKING_UNAVAILABLE", "Streams need the stream writer account")),
        drop=AsyncMock(return_value=None),
        list_streams=AsyncMock(return_value=[]),
        status=AsyncMock(return_value={"name": "analytics.s", "status": "READY"}),
        backlog=AsyncMock(return_value={"name": "analytics.s", "pending_commits": 0}),
        resolve=AsyncMock(return_value=SimpleNamespace(has_data=True)),
    )
    monkeypatch.setattr(service_module, "stream_service", fake)
    monkeypatch.setattr(sql_module, "stream_service", fake)
    monkeypatch.setattr(source_registry, "captured_tables", AsyncMock(return_value={}))
    monkeypatch.setattr(sql_module.stream_sql, "_stream_names", AsyncMock(return_value=set()))
    return fake


async def _run(monkeypatch, proxy: bool, sql: str, **kwargs):
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
        result = await service.execute(
            sql, "alice", "enc", database="analytics", role="reader", **kwargs
        )
    return result, repository, audit


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize(
    ("sql", "method"),
    [
        ("SHOW STREAMS", "list_streams"),
        ("DESCRIBE STREAM analytics.default.s", "status"),
        ("DESC STREAM analytics.s", "status"),
        ("SHOW STREAM STATUS analytics.default.s", "status"),
        ("SHOW STREAM BACKLOG analytics.s", "backlog"),
    ],
)
async def test_stream_statements_reach_the_service_not_the_engine(
    monkeypatch, streams, proxy, sql, method
):
    result, repository, _audit = await _run(monkeypatch, proxy, sql)
    assert result.error is None
    getattr(streams, method).assert_awaited_once()
    if method != "list_streams":
        assert getattr(streams, method).await_args.args[0] == StreamName("analytics", "s")
    repository.execute_as_user.assert_not_awaited()


@pytest.mark.parametrize("proxy", [False, True])
async def test_create_fails_closed_without_capture_and_audits_the_statement(
    monkeypatch, streams, proxy
):
    sql = "CREATE STREAM analytics.default.s ON TABLE analytics.default.t"
    result, repository, audit = await _run(monkeypatch, proxy, sql)
    assert result.error and "stream writer" in result.error
    if not proxy:  # the proxy answers with MySQL's numeric error codes
        assert result.error_code == "STREAM_CHANGE_TRACKING_UNAVAILABLE"
        assert result.original_sql == sql
    assert any(call.kwargs.get("sql_text") == sql for call in audit.await_args_list)
    repository.execute_as_user.assert_not_awaited()


@pytest.mark.parametrize("proxy", [False, True])
@pytest.mark.parametrize("function", ["SYSTEM$STREAM_HAS_DATA", "NOVA_STREAM_HAS_DATA"])
async def test_has_data_reaches_the_engine_as_a_literal(monkeypatch, streams, proxy, function):
    from app.modules.query.repository import QueryResult

    sql = f"SELECT {function}('analytics.default.s') AS ready"
    audit = AsyncMock()
    monkeypatch.setattr("app.modules.query.service.write_audit_log", audit)
    monkeypatch.setattr("app.modules.query.service.decrypt_password", lambda value: "pw")
    repository = AsyncMock()
    repository.execute_as_user.return_value = QueryResult(columns=["ready"], rows=[[1]])
    if proxy:
        monkeypatch.setattr(query_service, "_repo", repository)
        monkeypatch.setattr(
            query_service, "_capability_resolver", AsyncMock(return_value=EngineCapabilities())
        )
        await ProxyQueryExecutor(
            SessionState(database="analytics", active_role="reader"),
        ).execute(sql, username="alice", connection=object())
    else:
        service = QueryService(capability_resolver=AsyncMock(return_value=EngineCapabilities()))
        service._repo = repository
        await service.execute(sql, "alice", "enc", database="analytics", role="reader")
    sent = repository.execute_as_user.await_args.kwargs["sql"]
    assert sent == "SELECT TRUE AS ready"
    assert streams.resolve.await_args.args[0] == StreamName("analytics", "s")


@pytest.mark.parametrize("conditional", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
async def test_drop_confirmation_precedes_the_service(monkeypatch, streams, conditional, confirmed):
    from app.sql_frontend.errors import ConfirmationRequiredError

    sql = "DROP STREAM " + ("IF EXISTS " if conditional else "") + "analytics.s"
    if not confirmed:
        with pytest.raises(ConfirmationRequiredError):
            await _run(monkeypatch, False, sql)
        streams.drop.assert_not_awaited()
        return
    result, repository, _audit = await _run(monkeypatch, False, sql, confirm_destructive=True)
    assert result.error is None and result.original_sql == sql
    assert streams.drop.await_args.kwargs["if_exists"] is conditional
    repository.execute_as_user.assert_not_awaited()


@pytest.mark.parametrize("sql", ["DROP STREAM analytics.s", "DROP STREAM IF EXISTS analytics.s"])
async def test_proxy_drop_reaches_the_service_without_engine_submission(monkeypatch, streams, sql):
    # A MySQL client has no confirmation exchange, so the proxy submits its
    # statement as confirmed; the Streams service decides, never the engine.
    result, repository, _audit = await _run(monkeypatch, True, sql)
    assert result.error is None
    streams.drop.assert_awaited_once()
    repository.execute_as_user.assert_not_awaited()
