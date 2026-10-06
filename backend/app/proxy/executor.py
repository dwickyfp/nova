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

User-variable assignments are consumed in ``app.proxy.session`` before parsing.
System settings and database selection run on the authenticated engine connection.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import math
from ast import literal_eval
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.modules.access_control.role_activation import (
    RoleActivationError,
    role_activation_service,
)
from app.modules.query.admission import (
    REFUSAL,
    QueryCapacityError,
    audit_refusal,
    query_admission,
)
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
    TYPE_STRING,
    TYPE_TIME,
    TYPE_VAR_STRING,
    ColumnDefinition,
)
from app.proxy.session import (
    HIDDEN_DATABASES,
    PreparedSQL,
    SessionState,
    bind_placeholders,
    handle_set_statement,
    is_catalog_switch,
    is_show_databases,
    parse_prepared_statement,
    parse_role_statement,
    parse_use_statement,
    placeholder_spans,
    split_statements,
    substitute_user_variables,
    transaction_control,
    unquote_literal,
)
from app.sql_frontend.parser import run_sql_cpu
from app.sql_frontend.session_functions import CORRELATION_SESSION

logger = logging.getLogger(__name__)

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

    ``preceding`` holds the responses of the earlier statements of a
    multi-statement ``COM_QUERY``, in order. MySQL answers such a command with
    one result per statement, so a client reading the first result must get
    the first statement's, not the last one's.
    """

    columns: list[ColumnDefinition] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    ok_affected: int = 0
    error: str | None = None
    error_code: int = 1064
    warnings: list[str] = field(default_factory=list)
    preceding: list[WireResult] = field(default_factory=list)

    @property
    def is_resultset(self) -> bool:
        return bool(self.columns) and self.error is None

    @property
    def is_ok(self) -> bool:
        return not self.columns and self.error is None


def _column_definition(
    name: str, sample_values: list, native_description: str | None = None
) -> ColumnDefinition:
    """Preserve engine type metadata, inferring types for Nova-generated results.

    The type code is **not** cosmetic, which an earlier revision of this
    docstring got wrong. Clients dispatch on it: ``pymysql``'s converters map a
    type code to the Python object it builds, so a ``DECIMAL`` sent as
    ``TYPE_VAR_STRING`` arrives as ``'1.5'`` rather than ``Decimal('1.5')`` and
    breaks arithmetic (``'1.5' + 1`` raises ``TypeError``). The same is true of
    the temporal types, and ORMs such as SQLAlchemy and JDBC drivers map columns
    to model fields off this code.

    The fallback branches are ordered from most to least specific because several of
    these types are subclasses of each other — ``bool`` of ``int``,
    ``datetime`` of ``date`` — and testing the superclass first would collapse
    the narrower type.
    """
    if native_description:
        try:
            type_code, length, scale = literal_eval(native_description)
            if not isinstance(type_code, int) or not 0 <= type_code <= 255:
                raise ValueError("Invalid MySQL type")
            binary = type_code == TYPE_BLOB or any(
                isinstance(value, (bytes, bytearray)) for value in sample_values
            )
            return ColumnDefinition(
                name=name,
                type_code=type_code,
                charset=63 if binary else CHARSET_UTF8,
                column_length=max(0, min(int(1024 if length is None else length), 2**32 - 1)),
                decimals=max(0, min(int(scale or 0), 255)),
            )
        except (ValueError, TypeError, SyntaxError):
            pass
    for value in sample_values:
        if value is None:
            continue
        if isinstance(value, bool):
            return ColumnDefinition(name=name, type_code=TYPE_LONGLONG, charset=CHARSET_UTF8)
        if isinstance(value, int):
            return ColumnDefinition(name=name, type_code=TYPE_LONGLONG, charset=CHARSET_UTF8)
        if isinstance(value, decimal.Decimal):
            return ColumnDefinition(name=name, type_code=TYPE_NEWDECIMAL, charset=CHARSET_UTF8)
        if isinstance(value, float):
            return ColumnDefinition(name=name, type_code=TYPE_DOUBLE, charset=CHARSET_UTF8)
        # datetime.datetime before datetime.date: the former is a subclass.
        if isinstance(value, datetime.datetime):
            return ColumnDefinition(name=name, type_code=TYPE_DATETIME, charset=CHARSET_UTF8)
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


def _client_error_code(result: QueryResult) -> int:
    """The MySQL error number for a failed statement.

    An engine failure keeps the engine's own number (unknown database, unknown
    table, access denied, ...), which clients and drivers dispatch on.
    """
    code = result.engine_error_code
    if isinstance(code, int) and 0 < code <= 65535 and not 2000 <= code < 3000:
        return code
    return 1235 if result.error_code == "capability_unsupported" else 1064


def _client_message(result: QueryResult) -> str:
    """The engine's own message; its number travels in the ERR packet."""
    message = result.error or "Query failed"
    prefix = f"SQL error: ({result.engine_error_code}) "
    if result.engine_error_code is not None and message.startswith(prefix):
        return message.removeprefix(prefix)
    return message


class StatementStream:
    """One statement's response, written as it is produced.

    ``begin``/``rows`` make it a ``RowSink`` for the query pipeline, so an engine
    result set reaches the client batch by batch instead of after the whole
    result is in memory. ``finish`` completes the response from the statement's
    ``QueryResult``: the result-set terminator, an OK packet, or an ERR packet
    (which may follow rows already sent, as a MySQL server's does).

    Column definitions wait for the first batch so binary columns (geometry,
    VARBINARY) can still be recognised from their values.
    """

    def __init__(
        self,
        sink: ResponseSink,
        *,
        more: bool,
        row_limit: int,
        row_filter: Callable[[list], bool] | None = None,
    ) -> None:
        self._sink = sink
        self._more = more
        self._row_limit = row_limit
        self._row_filter = row_filter
        self._names: list[str] | None = None
        self._types: tuple[str, ...] = ()
        self._columns: list[ColumnDefinition] | None = None
        self._sent = 0
        self._overflow = False

    @property
    def overflowed(self) -> bool:
        return self._overflow

    async def begin(self, columns: list[str], column_types: tuple[str, ...]) -> None:
        self._names, self._types = list(columns), tuple(column_types)

    async def rows(self, rows: list[list]) -> None:
        if self._row_filter is not None:
            rows = [row for row in rows if self._row_filter(row)]
        if self._columns is None:
            self._columns = self._definitions(rows)
            await self._sink.columns(self._columns, more=self._more)
        if self._overflow or not rows:
            return
        if self._row_limit and self._sent + len(rows) > self._row_limit:
            # The remainder is drained by the pipeline and discarded.
            self._overflow = True
            return
        self._sent += len(rows)
        await self._sink.rows(rows, self._columns)

    def _definitions(self, samples: list[list]) -> list[ColumnDefinition]:
        names = self._names or []
        return [
            _column_definition(
                name,
                [row[index] for row in samples[:20]],
                self._types[index] if index < len(self._types) else None,
            )
            for index, name in enumerate(names)
        ]

    async def finish(self, part: WireResult) -> None:
        if self._names is None:
            await self._sink.emit(part, more=self._more)
            return
        if self._columns is None:
            await self.rows([])
        if part.error is not None:
            await self._sink.emit(part, more=False)
        elif self._overflow:
            await self._sink.emit(_row_limit_error(self._row_limit), more=False)
        else:
            await self._sink.end_rows(more=self._more)


def _visible_database(row: list) -> bool:
    return not row or str(row[0]) not in HIDDEN_DATABASES


def _row_limit_error(limit: int) -> WireResult:
    return WireResult(
        error=(
            f"Result set exceeds the Nova MySQL proxy limit of {limit} rows; "
            "add a LIMIT clause or raise PROXY_MAX_ROWS"
        ),
        error_code=1235,
    )


class ResponseSink(Protocol):
    """Where the responses of one ``COM_QUERY`` go, in statement order.

    ``emit`` writes a complete response (OK, buffered result set or ERR); an
    error ends the command. ``columns``/``rows``/``end_rows`` write a result set
    incrementally. ``more`` marks every response but the last of a
    multi-statement command.
    """

    async def emit(self, part: WireResult, *, more: bool) -> None: ...

    async def columns(self, columns: list[ColumnDefinition], *, more: bool) -> None: ...

    async def rows(self, rows: list[list], columns: list[ColumnDefinition]) -> None: ...

    async def end_rows(self, *, more: bool) -> None: ...


class CollectingSink:
    """Collects responses into a ``WireResult`` chain (``preceding`` + final)."""

    def __init__(self) -> None:
        self.parts: list[WireResult] = []
        self._open: WireResult | None = None

    async def emit(self, part: WireResult, *, more: bool) -> None:
        self.parts.append(part)

    async def columns(self, columns: list[ColumnDefinition], *, more: bool) -> None:
        self._open = WireResult(columns=list(columns))

    async def rows(self, rows: list[list], columns: list[ColumnDefinition]) -> None:
        assert self._open is not None
        self._open.rows.extend(list(row) for row in rows)

    async def end_rows(self, *, more: bool) -> None:
        assert self._open is not None
        self.parts.append(self._open)
        self._open = None

    def result(self) -> WireResult:
        if not self.parts:
            return WireResult(ok_affected=0)
        final = self.parts[-1]
        final.preceding = self.parts[:-1]
        return final


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
        """Run one ``COM_QUERY`` payload and return its responses as one chain."""
        sink = CollectingSink()
        await self.execute_to(
            sql, sink, username=username, connection=connection, session_id=session_id
        )
        return sink.result()

    async def execute_to(
        self,
        sql: str,
        sink: ResponseSink,
        *,
        username: str,
        connection,
        session_id: str | None = None,
    ) -> None:
        """Run one ``COM_QUERY`` payload, writing each response to ``sink``."""
        from app.sql_frontend.parser import parsing_scope

        with parsing_scope():
            await self._execute(
                sql, sink, username=username, connection=connection, session_id=session_id
            )

    async def _execute(
        self,
        sql: str,
        sink: ResponseSink,
        *,
        username: str,
        connection,
        session_id: str | None = None,
    ) -> None:
        """Run the statements of one payload in order, as a MySQL server does.

        Session statements the proxy owns (``SET @x``, ``SET ROLE``, ``USE``)
        apply between statements, so a later statement sees the database, role
        and variables set before it. A script is first checked as a whole —
        guard, syntax, semantics and session-setting refusals — so an invalid
        statement stops it before anything runs. Each statement then runs
        through ``QueryService`` on its own; execution stops at the first error.

        ``connection`` is the relay-authenticated StarRocks connection for this
        client. It is passed to the pipeline instead of a password because the
        proxy never has one (see ``app/proxy/auth.py``).
        """
        statements = split_statements(sql)
        if not statements:
            await sink.emit(WireResult(error="Empty query", error_code=1064), more=False)
            return
        if len(statements) > 1:
            refusal = await self._preflight(statements, username=username, session_id=session_id)
            if refusal is not None:
                await sink.emit(refusal, more=False)
                return
        last = len(statements) - 1
        for index, statement in enumerate(statements):
            ok = await self._run_statement(
                statement,
                sink,
                more=index < last,
                username=username,
                connection=connection,
                session_id=session_id,
            )
            if not ok:
                return

    async def _preflight(
        self, statements: list[str], *, username: str, session_id: str | None
    ) -> WireResult | None:
        """Refuse a script before its first statement runs."""
        preview = deepcopy(self._session)
        engine_statements: list[str] = []
        for statement in statements:
            try:
                if parse_role_statement(statement) is not None:
                    continue
            except ValueError as exc:
                return WireResult(error=str(exc), error_code=1064)
            if parse_use_statement(statement) is not None:
                engine_statements.append(statement)
                continue
            inspected = handle_set_statement(statement, preview)
            if inspected.error:
                return WireResult(error=inspected.error, error_code=inspected.error_code)
            if inspected.handled or parse_prepared_statement(statement) is not None:
                continue
            engine_statements.append(statement)
        if not engine_statements:
            return None
        refusal = await query_service.preflight_script(
            engine_statements,
            username=username,
            database=self._session.database,
            role=self._session.active_role,
            session_id=session_id,
        )
        return None if refusal is None else self._to_wire([refusal])

    async def _run_statement(
        self,
        statement: str,
        sink: ResponseSink,
        *,
        more: bool,
        username: str,
        connection,
        session_id: str | None,
    ) -> bool:
        """Run one statement and write its response; ``False`` stops the script."""
        try:
            requested_role = parse_role_statement(statement)
        except ValueError as exc:
            await sink.emit(WireResult(error=str(exc), error_code=1064), more=False)
            return False
        if requested_role is not None:
            if self._session.in_transaction:
                part = WireResult(
                    error="Role changes are not allowed inside a transaction", error_code=1235
                )
            else:
                part = await self._activate_role(
                    requested_role, username=username, connection=connection
                )
            return await self._emit(sink, part, more)

        use_result = await self._handle_use(
            statement, username=username, connection=connection, session_id=session_id
        )
        if use_result is not None:
            return await self._emit(sink, use_result, more)

        set_result = handle_set_statement(statement, self._session)
        if set_result.error:
            part = WireResult(error=set_result.error, error_code=set_result.error_code)
            return await self._emit(sink, part, more)
        if set_result.handled:
            part = WireResult()
            if set_result.assignment is not None:
                part = await self._assign_user_variable(
                    *set_result.assignment,
                    username=username,
                    connection=connection,
                    session_id=session_id,
                )
            return await self._emit(sink, part, more)

        prepared = parse_prepared_statement(statement)
        if prepared is not None:
            return await self._run_prepared_sql(
                prepared, sink, more=more, username=username, connection=connection,
                session_id=session_id,
            )

        # The read half of ``SET @x = …``: substitution happens here, after the
        # statements before it applied their assignments (NOVA-25).
        substituted = (
            await run_sql_cpu(
                len(statement), substitute_user_variables, statement, self._session
            )
        ).sql
        return await self.run_engine_statement(
            substituted,
            sink,
            more=more,
            username=username,
            connection=connection,
            session_id=session_id,
            catalog_switch=is_catalog_switch(statement),
            hide_databases=is_show_databases(statement),
        )

    async def run_engine_statement(
        self,
        sql: str,
        sink: ResponseSink,
        *,
        more: bool,
        username: str,
        connection,
        session_id: str | None,
        catalog_switch: bool = False,
        hide_databases: bool = False,
    ) -> bool:
        """Run one statement through ``QueryService``, streaming its rows."""
        row_limit = settings.PROXY_MAX_ROWS
        stream = StatementStream(
            sink,
            more=more,
            row_limit=row_limit,
            row_filter=_visible_database if hide_databases else None,
        )
        correlation_token = CORRELATION_SESSION.set(self._session.correlation)
        try:
            # A MySQL client has no confirmation exchange: the statement the user
            # sent is the explicit request, as with any MySQL server. The guard,
            # engine authorization and audit still apply to destructive SQL.
            # A statement inside an open transaction is always admitted: refusing
            # it would leave the client's transaction half applied.
            async with self._admission(username, session_id):
                results = await query_service.execute_statements(
                    source="mysql_proxy",
                    sql=sql,
                    username=username,
                    encrypted_password="",
                    database=self._session.database,
                    role=self._session.active_role,
                    security_context_version=self._session.security_context_version,
                    max_rows=row_limit or None,
                    session_id=session_id,
                    confirm_destructive=True,
                    connection=connection,
                    client_transaction=self._session.in_transaction,
                    row_sink=stream,
                )
        except QueryCapacityError:
            results = [QueryResult(error=REFUSAL)]
        except Exception as exc:
            # A failure outside the per-statement loop (the connection dying, or
            # the pipeline raising before it builds a result). The message can
            # carry engine detail, so it is logged and the client gets 1064.
            logger.exception("Proxy query execution failed")
            results = [QueryResult(error=f"Query execution failed: {type(exc).__name__}")]
        finally:
            CORRELATION_SESSION.reset(correlation_token)
        part = self._to_wire(results[-1:], row_limit=row_limit or None)
        control = transaction_control(sql)
        if control == "end":
            self._session.in_transaction = False
        elif control == "begin" and part.error is None:
            self._session.in_transaction = True
        if hide_databases and part.is_resultset:
            part.rows = [row for row in part.rows if _visible_database(row)]
        await stream.finish(part)
        if part.error is None and catalog_switch:
            # The selected database belonged to the previous catalog.
            self._session.set_database("")
        return part.error is None and not stream.overflowed

    @asynccontextmanager
    async def _admission(self, username: str, session_id: str | None) -> AsyncIterator[None]:
        if self._session.in_transaction:
            yield
            return
        try:
            async with query_admission.slot("mysql_proxy"):
                yield
        except QueryCapacityError:
            await audit_refusal(
                username=username, source="mysql_proxy", target="", session_id=session_id
            )
            raise

    async def _run_prepared_sql(
        self,
        command: PreparedSQL,
        sink: ResponseSink,
        *,
        more: bool,
        username: str,
        connection,
        session_id: str | None,
    ) -> bool:
        """``PREPARE``/``EXECUTE``/``DEALLOCATE PREPARE`` kept on the proxy session.

        The prepared text never reaches the engine as a prepared statement:
        ``EXECUTE`` binds the arguments as literals and runs the result like any
        statement the client sent, so it gets the same guard, parsing,
        authorization and audit.
        """
        prepared = self._session.prepared
        if command.kind == "deallocate":
            if prepared.pop(command.name, None) is None:
                part = WireResult(error="Unknown prepared statement handler", error_code=1243)
            else:
                part = WireResult()
            return await self._emit(sink, part, more)
        if command.kind == "prepare":
            source = command.source or ""
            text = unquote_literal(source)
            if text is None and source.startswith("@"):
                stored = self._session.user_variables.get(source[1:].strip().lower())
                text = unquote_literal(stored) if stored else None
            if text is None:
                part = WireResult(
                    error="PREPARE needs a string literal or a string user variable",
                    error_code=1064,
                )
                return await self._emit(sink, part, more)
            if len(split_statements(text)) != 1 or parse_prepared_statement(text) is not None:
                part = WireResult(error="A prepared statement holds one statement", error_code=1064)
                return await self._emit(sink, part, more)
            prepared[command.name] = text
            return await self._emit(sink, WireResult(), more)
        text = prepared.get(command.name)
        if text is None:
            part = WireResult(error="Unknown prepared statement handler", error_code=1243)
            return await self._emit(sink, part, more)
        arguments = [
            self._session.user_variables.get(name)
            or (f"@{name}" if name in self._session.engine_variables else "NULL")
            for name in command.arguments
        ]
        if len(arguments) != len(placeholder_spans(text)):
            part = WireResult(error="Incorrect arguments to EXECUTE", error_code=1210)
            return await self._emit(sink, part, more)
        return await self._run_statement(
            bind_placeholders(text, arguments),
            sink,
            more=more,
            username=username,
            connection=connection,
            session_id=session_id,
        )

    @staticmethod
    async def _emit(sink: ResponseSink, part: WireResult, more: bool) -> bool:
        await sink.emit(part, more=more and part.error is None)
        return part.error is None

    async def _assign_user_variable(
        self, name: str, expression: str, *, username: str, connection, session_id: str | None
    ) -> WireResult:
        expression = substitute_user_variables(expression, self._session).sql
        token = CORRELATION_SESSION.set(self._session.correlation)
        try:
            results = await query_service.execute_statements(
                source="mysql_proxy",
                sql=f"SELECT ({expression}) AS nova_user_variable",
                username=username,
                encrypted_password="",
                database=self._session.database,
                role=self._session.active_role,
                security_context_version=self._session.security_context_version,
                max_rows=2,
                session_id=session_id,
                connection=connection,
                client_transaction=self._session.in_transaction,
            )
            result = self._to_wire(results)
            if result.error:
                return result
            if len(result.rows) != 1 or len(result.rows[0]) != 1:
                return WireResult(error="A user-variable assignment requires one scalar value")
            value = result.rows[0][0]
            if value is None:
                literal = "NULL"
            elif isinstance(value, (bytes, bytearray)):
                literal = f"unhex('{bytes(value).hex()}')"
            elif isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
                literal = "'" + value.isoformat() + "'"
            elif isinstance(value, bool):
                literal = "TRUE" if value else "FALSE"
            elif isinstance(value, (int, float, decimal.Decimal)):
                if (isinstance(value, float) and not math.isfinite(value)) or (
                    isinstance(value, decimal.Decimal) and not value.is_finite()
                ):
                    return WireResult(error="Non-finite user-variable value", error_code=1235)
                literal = str(value)
            elif isinstance(value, str) and result.columns[0].type_code == TYPE_STRING:
                # ARRAY, MAP, STRUCT, JSON and LARGEINT arrive as text with the
                # STRING type code. A quoted literal would turn them into VARCHAR,
                # so the engine session keeps the typed value instead.
                return await self._assign_engine_variable(
                    name, expression, username=username, connection=connection,
                    session_id=session_id,
                )
            elif isinstance(value, str):
                literal = "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"
            else:
                return WireResult(error="Unsupported user-variable value type", error_code=1235)
            self._session.user_variables[name] = literal
            self._session.engine_variables.discard(name)
            return WireResult()
        except Exception as exc:
            return WireResult(error=f"User-variable assignment failed: {type(exc).__name__}")
        finally:
            CORRELATION_SESSION.reset(token)

    async def _assign_engine_variable(
        self, name: str, expression: str, *, username: str, connection, session_id: str | None
    ) -> WireResult:
        """Store a typed value as an engine session variable.

        The central parser reads ``@name`` in an expression as a user variable,
        never a stage, so later references reach the engine unchanged and it
        resolves them with their original type.
        """
        results = await query_service.execute_statements(
            source="mysql_proxy",
            sql=f"SET @{name} = {expression}",
            username=username,
            encrypted_password="",
            database=self._session.database,
            role=self._session.active_role,
            security_context_version=self._session.security_context_version,
            session_id=session_id,
            connection=connection,
            client_transaction=self._session.in_transaction,
        )
        result = self._to_wire(results)
        if result.error is None:
            self._session.user_variables.pop(name, None)
            self._session.engine_variables.add(name)
        return result

    async def _activate_role(self, requested: str, *, username: str, connection) -> WireResult:
        target = self._session.default_role if requested.upper() == "DEFAULT" else requested
        if not target:
            return WireResult(error="No explicit default role is configured", error_code=1045)
        try:
            active, assignments = await role_activation_service.activate(
                connection,
                principal=username,
                requested_role=target,
                known_default_role=self._session.default_role,
            )
        except RoleActivationError as exc:
            return WireResult(error=str(exc), error_code=1045)
        self._session.assigned_roles = assignments.assigned_roles
        self._session.default_role = assignments.default_role
        self._session.commit_role(active)
        return WireResult(ok_affected=0)

    async def _handle_use(
        self, statement: str, *, username: str, connection, session_id: str | None
    ) -> WireResult | None:
        database = parse_use_statement(statement)
        if database is None:
            return None
        try:
            results = await query_service.execute_statements(
                source="mysql_proxy",
                sql=statement,
                username=username,
                encrypted_password="",
                database=self._session.database,
                role=self._session.active_role,
                security_context_version=self._session.security_context_version,
                max_rows=settings.PROXY_MAX_ROWS,
                session_id=session_id,
                connection=connection,
                client_transaction=self._session.in_transaction,
            )
        except Exception as exc:
            return WireResult(error=f"Database selection failed: {type(exc).__name__}")
        result = self._to_wire(results)
        if result.error:
            return result
        self._session.set_database(database)
        return result

    def _to_wire(self, results: list[QueryResult], *, row_limit: int | None = None) -> WireResult:
        """Map ``QueryResult`` objects to the response of one ``COM_QUERY``.

        Each statement becomes its own result set or OK packet; the earlier
        ones ride in ``preceding``. The pipeline stops at the first error, so an
        error is always the final response, after the results of the statements
        that did run — the same shape a MySQL server sends.
        """
        if not results:
            return WireResult(ok_affected=0)
        parts: list[WireResult] = []
        for result in results:
            part = self._result_to_wire(result, row_limit=row_limit)
            parts.append(part)
            if part.error:
                break
        final = parts.pop()
        final.preceding = parts
        return final

    def _result_to_wire(self, result: QueryResult, *, row_limit: int | None) -> WireResult:
        if result.error:
            return WireResult(
                error=self._safe_message(_client_message(result)),
                error_code=_client_error_code(result),
            )
        if row_limit is not None and result.truncated:
            return WireResult(
                error=(
                    f"Result set exceeds the Nova MySQL proxy limit of {row_limit} rows; "
                    "add a LIMIT clause or raise PROXY_MAX_ROWS"
                ),
                error_code=1235,
            )
        if result.columns:
            columns = [
                _column_definition(
                    name,
                    [row[index] for row in result.rows[:20]] if result.rows else [],
                    result.column_types[index] if index < len(result.column_types) else None,
                )
                for index, name in enumerate(result.columns)
            ]
            return WireResult(
                columns=columns,
                rows=[list(row) for row in result.rows],
                warnings=self._safe_warnings(result),
            )
        return WireResult(
            ok_affected=max(int(result.affected_rows or 0), 0),
            warnings=self._safe_warnings(result),
        )

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
    "ProxyQueryExecutor",
    "WireResult",
]
