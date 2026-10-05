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
from copy import deepcopy
from dataclasses import dataclass, field

from app.common.sql_guard import redact_sql_credentials
from app.core.config import settings
from app.modules.access_control.role_activation import (
    RoleActivationError,
    role_activation_service,
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
    STATEMENT_SEPARATOR,
    SessionState,
    handle_set_statement,
    is_catalog_switch,
    is_show_databases,
    parse_role_statement,
    parse_use_statement,
    split_statements,
    substitute_user_variables,
)
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
        from app.sql_frontend.parser import parsing_scope

        with parsing_scope():
            return await self._execute(
                sql, username=username, connection=connection, session_id=session_id
            )

    async def _execute(
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

        if len(statements) > 1:
            preview = deepcopy(self._session)
            engine_seen = False
            for statement in statements:
                try:
                    context_change = (
                        parse_role_statement(statement) is not None
                        or parse_use_statement(statement) is not None
                        or is_catalog_switch(statement)
                    )
                except ValueError as exc:
                    return WireResult(error=str(exc), error_code=1064)
                if context_change:
                    return WireResult(
                        error="Send database and role changes as separate MySQL commands",
                        error_code=1235,
                    )
                inspected = handle_set_statement(statement, preview)
                if inspected.error:
                    return WireResult(error=inspected.error, error_code=inspected.error_code)
                if inspected.handled and engine_seen:
                    return WireResult(
                        error="Session assignments after queries require separate MySQL commands",
                        error_code=1235,
                    )
                engine_seen = engine_seen or not inspected.handled

        engine_statements: list[str] = []
        # Session-local statements precede every engine statement in a script
        # (checked above); each still owes the client its own OK packet.
        local_results: list[WireResult] = []
        for statement in statements:
            try:
                requested_role = parse_role_statement(statement)
            except ValueError as exc:
                return WireResult(error=str(exc), error_code=1064)
            if requested_role is not None:
                activated = await self._activate_role(
                    requested_role,
                    username=username,
                    connection=connection,
                )
                if activated.error:
                    return activated
                continue

            use_result = await self._handle_use(
                statement, username=username, connection=connection, session_id=session_id
            )
            if use_result is not None:
                return use_result

            set_result = handle_set_statement(statement, self._session)
            if set_result.error:
                return WireResult(
                    error=set_result.error,
                    error_code=set_result.error_code,
                    preceding=local_results,
                )
            if set_result.handled:
                if set_result.assignment is not None:
                    assigned = await self._assign_user_variable(
                        *set_result.assignment,
                        username=username,
                        connection=connection,
                        session_id=session_id,
                    )
                    if assigned.error:
                        assigned.preceding = local_results
                        return assigned
                local_results.append(WireResult())
                continue

            # The read half of ``SET @x = …``. Substitution happens after the
            # ``SET``/``USE`` classification so a statement the proxy owns never
            # reaches the engine, and before it is queued so the engine sees the
            # value rather than a reference Nova's own parser would claim as a
            # stage (NOVA-25).
            substituted = substitute_user_variables(statement, self._session)
            if substituted.sql.lstrip().upper().startswith(("PREPARE ", "EXECUTE ")):
                return WireResult(
                    error="Prepared statements are not supported by the Nova MySQL proxy",
                    error_code=1235,
                )
            engine_statements.append(substituted.sql)

        if not engine_statements:
            # Every statement was session-local (a script of ``SET``s, or a
            # role change).
            if not local_results:
                return WireResult(ok_affected=0)
            final = local_results.pop()
            final.preceding = local_results
            return final

        sql_to_run = STATEMENT_SEPARATOR.join(engine_statements)

        if len(engine_statements) == 1 and is_show_databases(engine_statements[0]):
            final = await self._show_databases(username=username, connection=connection)
            final.preceding = local_results + final.preceding
            return final

        correlation_token = CORRELATION_SESSION.set(self._session.correlation)
        row_limit = settings.PROXY_MAX_ROWS
        try:
            # A MySQL client has no confirmation exchange: the statement the user
            # sent is the explicit request, as with any MySQL server. The guard,
            # engine authorization and audit still apply to destructive SQL.
            results = await query_service.execute_statements(
                source="mysql_proxy",
                sql=sql_to_run,
                username=username,
                encrypted_password="",
                database=self._session.database,
                role=self._session.active_role,
                security_context_version=self._session.security_context_version,
                max_rows=row_limit,
                session_id=session_id,
                confirm_destructive=True,
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

        finally:
            CORRELATION_SESSION.reset(correlation_token)

        final = self._to_wire(results, row_limit=row_limit)
        if final.error is None and len(statements) == 1 and is_catalog_switch(statements[0]):
            # The selected database belonged to the previous catalog.
            self._session.set_database("")
        final.preceding = local_results + final.preceding
        return final

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
            )
        except Exception as exc:
            return WireResult(error=f"Database selection failed: {type(exc).__name__}")
        result = self._to_wire(results)
        if result.error:
            return result
        self._session.set_database(database)
        return result

    async def _show_databases(self, *, username: str, connection) -> WireResult:
        """Answer ``SHOW DATABASES`` with Nova-internal databases filtered out.

        Runs through ``QueryService`` like any other statement, so the
        ``SHOW DATABASES`` is itself guarded and audited; only the filtering
        happens here. The statement runs as the authenticated user (the relayed
        connection *is* that user's session), so the list is exactly what that
        user may see minus the names in ``HIDDEN_DATABASES``.
        """
        correlation_token = CORRELATION_SESSION.set(self._session.correlation)
        try:
            results = await query_service.execute_statements(
                source="mysql_proxy",
                sql="SHOW DATABASES",
                username=username,
                encrypted_password="",
                database=self._session.database,
                role=self._session.active_role,
                security_context_version=self._session.security_context_version,
                max_rows=settings.PROXY_MAX_ROWS,
                connection=connection,
            )
        except Exception as exc:
            logger.exception("SHOW DATABASES through proxy failed")
            return WireResult(
                error=f"Query execution failed: {type(exc).__name__}", error_code=1064
            )

        finally:
            CORRELATION_SESSION.reset(correlation_token)

        wire = self._to_wire(results, row_limit=settings.PROXY_MAX_ROWS)
        if not wire.is_resultset:
            return wire

        first = results[0]
        kept_rows = [row for row in first.rows if row and str(row[0]) not in HIDDEN_DATABASES]
        return WireResult(
            columns=[
                ColumnDefinition(name="Database", type_code=TYPE_VAR_STRING, charset=CHARSET_UTF8)
            ],
            rows=kept_rows,
        )

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
