"""Bridge from proxy statements to ``QueryService`` and back to the wire.

Every engine call the proxy makes goes through :class:`ProxyQueryExecutor`,
which owns exactly three responsibilities and delegates all of them:

* routing each statement to ``QueryService.execute_statements`` (the one
  pipeline that runs the ACCOUNTADMIN guard, the ``@stage`` → ``FILES()``
  translation, credential injection and the audit write — the proxy must not
  reach the engine any other way, or a query would bypass the guard and leave
  no audit row);
* handing that pipeline the connection the proxy authenticated **by relay**
  (``app/proxy/auth.py``), which is why it can never pass a password; and
* keeping credentials off the wire — the engine statement carries real storage
  credentials, and ``QueryResult.executed_sql`` is already redacted by its
  constructor, but this module is the last hop, so it re-runs
  ``redact_sql_credentials`` on anything it is about to place in a response.

``SET`` and ``USE`` never arrive here. They are consumed in
``app.proxy.session`` because the dialect parser would misread ``SET @x = 1``
as a stage reference.
"""

from __future__ import annotations

import datetime
import decimal
import logging
from dataclasses import dataclass, field

from app.common.sql_guard import redact_sql_credentials
from app.modules.query.repository import QueryResult
from app.modules.query.service import query_service
from app.proxy.protocol import (
    CHARSET_UTF8,
    TYPE_BLOB,
    TYPE_DATE,
    TYPE_DATETIME,
    TYPE_DOUBLE,
    TYPE_LONGLONG,
    TYPE_NEWDECIMAL,
    TYPE_TIME,
    TYPE_VAR_STRING,
    ColumnDefinition,
)
from app.proxy.session import (
    HIDDEN_DATABASES,
    SessionState,
    handle_set_statement,
    is_show_databases,
    parse_use_statement,
    split_statements,
    substitute_user_variables,
)

logger = logging.getLogger(__name__)

#: Rows the proxy will pull per statement. Matches the HTTP API's ceiling
#: (``backend/app/modules/query/router.py``) so the same query returns the same
#: volume whichever surface it arrives through.
DEFAULT_MAX_ROWS = 500

_INTEGER_COLUMNS = frozenset(
    {"tinyint", "smallint", "mediumint", "int", "integer", "bigint", "largeint"}
)
_DECIMAL_COLUMNS = frozenset({"decimal", "numeric", "float", "double", "real"})


@dataclass
class WireResult:
    """A single response the connection loop will write to the socket.

    Exactly one of ``columns``/``rows`` (a result set) or ``error`` (an ERR
    packet) is set. ``ok_affected`` is the OK-packet row count for statements
    that produced no result set.
    """

    columns: list[ColumnDefinition] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    ok_affected: int = 0
    error: str | None = None
    error_code: int = 1064
    warnings: list[str] = field(default_factory=list)

    @property
    def is_resultset(self) -> bool:
        return bool(self.columns) and self.error is None

    @property
    def is_ok(self) -> bool:
        return not self.columns and self.error is None


def _column_definition(name: str, sample_values: list) -> ColumnDefinition:
    """Describe a column using a sample of its values.

    ``QueryResult`` carries column *names* and Python values, not StarRocks type
    metadata, so the type is inferred from the first non-null value.

    The type code is **not** cosmetic, which an earlier revision of this
    docstring got wrong. Clients dispatch on it: ``pymysql``'s converters map a
    type code to the Python object it builds, so a ``DECIMAL`` sent as
    ``TYPE_VAR_STRING`` arrives as ``'1.5'`` rather than ``Decimal('1.5')`` and
    breaks arithmetic (``'1.5' + 1`` raises ``TypeError``). The same is true of
    the temporal types, and ORMs such as SQLAlchemy and JDBC drivers map columns
    to model fields off this code.

    The branches are ordered from most to least specific because several of
    these types are subclasses of each other — ``bool`` of ``int``,
    ``datetime`` of ``date`` — and testing the superclass first would collapse
    the narrower type.
    """
    for value in sample_values:
        if value is None:
            continue
        if isinstance(value, bool):
            return ColumnDefinition(name=name, type_code=TYPE_LONGLONG, charset=CHARSET_UTF8)
        if isinstance(value, int):
            return ColumnDefinition(name=name, type_code=TYPE_LONGLONG, charset=CHARSET_UTF8)
        if isinstance(value, decimal.Decimal):
            return ColumnDefinition(
                name=name, type_code=TYPE_NEWDECIMAL, charset=CHARSET_UTF8
            )
        if isinstance(value, float):
            return ColumnDefinition(name=name, type_code=TYPE_DOUBLE, charset=CHARSET_UTF8)
        # datetime.datetime before datetime.date: the former is a subclass.
        if isinstance(value, datetime.datetime):
            return ColumnDefinition(
                name=name, type_code=TYPE_DATETIME, charset=CHARSET_UTF8
            )
        if isinstance(value, datetime.date):
            return ColumnDefinition(name=name, type_code=TYPE_DATE, charset=CHARSET_UTF8)
        if isinstance(value, datetime.timedelta):
            return ColumnDefinition(name=name, type_code=TYPE_TIME, charset=CHARSET_UTF8)
        if isinstance(value, (bytes, bytearray)):
            return ColumnDefinition(name=name, type_code=TYPE_BLOB, charset=63)
        if isinstance(value, datetime.time):
            return ColumnDefinition(name=name, type_code=TYPE_TIME, charset=CHARSET_UTF8)
        break

    return ColumnDefinition(name=name, type_code=TYPE_VAR_STRING, charset=CHARSET_UTF8)


class ProxyQueryExecutor:
    """Executes client SQL through the Nova query pipeline."""

    def __init__(self, session: SessionState) -> None:
        self._session = session

    async def execute(
        self,
        sql: str,
        *,
        username: str,
        connection,
        session_id: str | None = None,
    ) -> WireResult:
        """Run one ``COM_QUERY`` payload and return the response.

        The payload is split by the proxy first so that ``SET``, ``USE`` and
        ``SHOW DATABASES`` can be handled locally. Everything else is handed to
        ``QueryService.execute_statements`` *as a unit* rather than statement by
        statement: the service already splits internally and stops at the first
        error, and re-splitting here would double-execute the audit and guard
        paths.

        ``connection`` is the relay-authenticated StarRocks connection for this
        client. It is passed to the pipeline instead of a password because the
        proxy never has one (see ``app/proxy/auth.py``).
        """
        statements = split_statements(sql)
        if not statements:
            return WireResult(error="Empty query", error_code=1064)

        engine_statements: list[str] = []
        for statement in statements:
            use_result = self._handle_use(statement)
            if use_result is not None:
                return use_result

            set_result = handle_set_statement(statement, self._session)
            if set_result.error:
                return WireResult(error=set_result.error, error_code=1064)
            if set_result.handled:
                continue

            # The read half of ``SET @x = …``. Substitution happens after the
            # ``SET``/``USE`` classification so a statement the proxy owns never
            # reaches the engine, and before it is queued so the engine sees the
            # value rather than a reference Nova's own parser would claim as a
            # stage (NOVA-25).
            substituted = substitute_user_variables(statement, self._session)
            engine_statements.append(substituted.sql)

        if not engine_statements:
            # Every statement was session-local (a script of ``SET``s, say).
            return WireResult(ok_affected=0)

        sql_to_run = "; ".join(engine_statements)

        if len(engine_statements) == 1 and is_show_databases(engine_statements[0]):
            return await self._show_databases(username=username, connection=connection)

        try:
            results = await query_service.execute_statements(
                sql=sql_to_run,
                username=username,
                encrypted_password="",
                database=self._session.database,
                role=self._session.active_role,
                max_rows=DEFAULT_MAX_ROWS,
                session_id=session_id,
                connection=connection,
            )
        except Exception as exc:
            # A failure *outside* the per-statement loop — the connection dying,
            # or the pipeline raising before it builds a result. The exception's
            # own message can carry engine detail, so it is logged and the client
            # gets a generic 1064.
            logger.exception("Proxy query execution failed")
            return WireResult(
                error=f"Query execution failed: {type(exc).__name__}", error_code=1064
            )

        return self._to_wire(results)

    def _handle_use(self, statement: str) -> WireResult | None:
        database = parse_use_statement(statement)
        if database is None:
            return None
        self._session.set_database(database)
        return WireResult(ok_affected=0)

    async def _show_databases(self, *, username: str, connection) -> WireResult:
        """Answer ``SHOW DATABASES`` with Nova-internal databases filtered out.

        Runs through ``QueryService`` like any other statement, so the
        ``SHOW DATABASES`` is itself guarded and audited; only the filtering
        happens here. The statement runs as the authenticated user (the relayed
        connection *is* that user's session), so the list is exactly what that
        user may see minus the names in ``HIDDEN_DATABASES``.
        """
        try:
            results = await query_service.execute_statements(
                sql="SHOW DATABASES",
                username=username,
                encrypted_password="",
                database=self._session.database,
                role=self._session.active_role,
                max_rows=DEFAULT_MAX_ROWS,
                connection=connection,
            )
        except Exception as exc:
            logger.exception("SHOW DATABASES through proxy failed")
            return WireResult(
                error=f"Query execution failed: {type(exc).__name__}", error_code=1064
            )

        wire = self._to_wire(results)
        if not wire.is_resultset:
            return wire

        first = results[0]
        kept_rows = [
            row for row in first.rows if row and str(row[0]) not in HIDDEN_DATABASES
        ]
        return WireResult(
            columns=[
                ColumnDefinition(
                    name="Database", type_code=TYPE_VAR_STRING, charset=CHARSET_UTF8
                )
            ],
            rows=kept_rows,
        )

    def _to_wire(self, results: list[QueryResult]) -> WireResult:
        """Map ``QueryResult`` objects to one response.

        A single statement's result becomes that statement's response. A
        multi-statement script collapses to the *last* result set (or OK), which
        is what the MySQL text protocol can express without ``CLIENT_MULTI_RESULTS``
        bookkeeping; an error at any point wins, because the first error is
        where the engine stopped and later statements never ran.
        """
        if not results:
            return WireResult(ok_affected=0)

        for result in results:
            if result.error:
                return WireResult(error=self._safe_message(result.error), error_code=1064)

        last = results[-1]
        if last.columns:
            columns = [
                _column_definition(
                    name,
                    [row[index] for row in last.rows[:20]] if last.rows else [],
                )
                for index, name in enumerate(last.columns)
            ]
            return WireResult(
                columns=columns,
                rows=[list(row) for row in last.rows],
                warnings=self._safe_warnings(last),
            )

        affected = sum(int(result.affected_rows or 0) for result in results)
        return WireResult(ok_affected=max(affected, 0), warnings=self._safe_warnings(last))

    @staticmethod
    def _safe_message(message: str) -> str:
        """Redact credential-looking text out of an engine error message.

        Errors are echoed to the client, and a failed ``@stage`` query can echo
        the statement the engine rejected — which carries storage credentials.
        Redaction here is the last gate before the socket; the audit row is
        already redacted upstream by ``QueryService``.
        """
        try:
            return redact_sql_credentials(message)
        except Exception:
            return "Query failed"

    @staticmethod
    def _safe_warnings(result: QueryResult) -> list[str]:
        warnings: list[str] = []
        for warning in result.warnings or []:
            try:
                warnings.append(redact_sql_credentials(warning))
            except Exception:
                warnings.append("warning redacted")
        return warnings


__all__ = [
    "DEFAULT_MAX_ROWS",
    "ProxyQueryExecutor",
    "WireResult",
]
