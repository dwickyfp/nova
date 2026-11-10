"""Proxy compatibility for clients that bind results at prepare time.

DuckDB's ``mysql`` scanner (the Nova Notebook's native path to Nova data) and
server-side-prepare JDBC read a query's columns from ``COM_STMT_PREPARE`` and
open ``START TRANSACTION READ ONLY`` around reads. These tests pin both:

* a prepared query announces its columns, described through the same governed
  pipeline with ``LIMIT 0`` (no rows read), and anything else describes nothing;
* a read-only transaction is answered by the proxy, refuses writes with MySQL's
  own error, and closes on ``COMMIT``/``ROLLBACK`` without reaching the engine.
"""

from __future__ import annotations

import struct

import pytest

from app.modules.query.repository import QueryResult
from app.proxy import protocol as p
from app.proxy.executor import ProxyQueryExecutor
from app.proxy.session import SessionState, is_read_only_transaction_start


class RecordingQueryService:
    def __init__(self, result: QueryResult | None = None) -> None:
        self.calls: list[str] = []
        self._result = result or QueryResult()

    async def preflight_script(self, statements, **kwargs):
        return None

    async def execute_statements(self, **kwargs):
        self.calls.append(kwargs["sql"])
        return [self._result]


@pytest.fixture
def service(monkeypatch):
    fake = RecordingQueryService(
        QueryResult(columns=["id", "name"], rows=[], column_types=("BIGINT", "VARCHAR"))
    )
    monkeypatch.setattr("app.proxy.executor.query_service", fake)
    return fake


def test_prepare_ok_announces_result_columns_after_parameters():
    columns = [p.ColumnDefinition(name="id", type_code=p.TYPE_LONGLONG), p.ColumnDefinition("n")]
    payloads = p.build_prepare_ok(3, 1, capabilities=p.SERVER_CAPABILITIES, columns=columns)
    header = payloads[0]
    assert struct.unpack("<H", header[5:7])[0] == 2  # num_columns
    assert struct.unpack("<H", header[7:9])[0] == 1  # num_params
    # header, 1 parameter + EOF, 2 columns + EOF (no CLIENT_DEPRECATE_EOF here)
    assert len(payloads) == 1 + 2 + 3
    assert payloads[-1][0] == 0xFE


def test_prepare_ok_without_columns_keeps_the_previous_shape():
    payloads = p.build_prepare_ok(9, 0, capabilities=p.SERVER_CAPABILITIES)
    assert payloads == [payloads[0]] and struct.unpack("<H", payloads[0][5:7])[0] == 0


async def test_a_prepared_query_is_described_with_limit_zero(service):
    executor = ProxyQueryExecutor(SessionState(active_role="reader"))
    columns = await executor.describe_columns(
        "SELECT id, name FROM t WHERE id > NULL", username="alice", connection=object(),
        session_id="s",
    )
    assert [c.name for c in columns] == ["id", "name"]
    assert service.calls == [
        "SELECT * FROM (SELECT id, name FROM t WHERE id > NULL) AS nova_prepare_describe LIMIT 0"
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "SHOW TABLES",
        "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
        "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    ],
)
async def test_statements_that_are_not_plain_reads_describe_nothing(service, sql):
    executor = ProxyQueryExecutor(SessionState())
    described = await executor.describe_columns(
        sql, username="a", connection=object(), session_id=None
    )
    assert described == []
    assert service.calls == []


async def test_a_refused_describe_falls_back_to_no_columns(monkeypatch):
    fake = RecordingQueryService(QueryResult(error="Access denied"))
    monkeypatch.setattr("app.proxy.executor.query_service", fake)
    executor = ProxyQueryExecutor(SessionState())
    assert await executor.describe_columns(
        "SELECT secret FROM t", username="a", connection=object(), session_id=None
    ) == []


@pytest.mark.parametrize(
    "sql",
    [
        "START TRANSACTION READ ONLY",
        "start transaction read only;",
        "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY",
        "/* duckdb */ START TRANSACTION READ ONLY",
    ],
)
def test_read_only_transaction_starts_are_recognised(sql):
    assert is_read_only_transaction_start(sql)


@pytest.mark.parametrize("sql", ["START TRANSACTION", "BEGIN", "START TRANSACTION READ WRITE"])
def test_other_transaction_starts_are_not_read_only(sql):
    assert not is_read_only_transaction_start(sql)


async def test_read_only_transaction_is_answered_without_the_engine(service):
    session = SessionState(active_role="reader")
    executor = ProxyQueryExecutor(session)
    started = await executor.execute(
        "START TRANSACTION READ ONLY", username="alice", connection=object()
    )
    assert started.is_ok and session.read_only_transaction and not session.in_transaction
    refused = await executor.execute(
        "DELETE FROM t WHERE id = 1", username="alice", connection=object()
    )
    assert refused.error_code == 1792
    read = await executor.execute("SELECT id, name FROM t", username="alice", connection=object())
    assert read.is_resultset
    ended = await executor.execute("COMMIT", username="alice", connection=object())
    assert ended.is_ok and not session.read_only_transaction
    assert service.calls == ["SELECT id, name FROM t"]


async def test_read_only_transaction_cannot_open_inside_an_engine_transaction(service):
    session = SessionState(in_transaction=True)
    result = await ProxyQueryExecutor(session).execute(
        "START TRANSACTION READ ONLY", username="alice", connection=object()
    )
    assert result.error_code == 1568 and not session.read_only_transaction
